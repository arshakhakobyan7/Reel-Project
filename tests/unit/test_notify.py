"""Staff outbox with real queue transitions and fixture databases, never real Slack."""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from reel.config import load_config
from reel.db import AppMeta, EventKind, Job, JobEvent, Recording, Stage, StageRun, utcnow
from reel.db.enums import StageRunStatus
from reel.notify import StaffNotifier
from reel.notify.staff import PENDING, WATERMARK
from reel.publish.slack import SlackError
from reel.queue import JobQueue, RetryPolicy

TOKEN = "synthetic-staff-slack-token"
OTHER_TOKEN = "synthetic-other-api-token"
CHANNEL = "C0123456789"
NOW = datetime(2026, 11, 19, 15, 0, tzinfo=UTC)


class Client:
    def __init__(self):
        self.messages = []
        self.attempts = []
        self.histories = []
        self.failures = []
        self.history_failures = []
        self.pages = []
        self.lose_response = False
        self.on_post = None
        self.sent = threading.Event()

    def history(self, channel, *, cursor="", limit=100):
        self.histories.append((channel, cursor, limit))
        if self.history_failures:
            raise self.history_failures.pop(0)
        if self.pages:
            return self.pages.pop(0)
        return {"ok": True, "messages": deepcopy(self.messages), "has_more": False}

    def post_message(self, channel, text, metadata):
        self.attempts.append((channel, text, deepcopy(metadata)))
        if self.failures:
            raise self.failures.pop(0)
        if self.on_post is not None:
            self.on_post()
        ts = f"1700000000.{len(self.messages) + 1:06d}"
        self.messages.append(
            {"channel": channel, "text": text, "metadata": deepcopy(metadata), "ts": ts}
        )
        self.sent.set()
        if self.lose_response:
            self.lose_response = False
            raise SlackError("connection_error", retryable=True)
        return {"ok": True, "channel": channel, "ts": ts}


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def configuration(project, write_config):
    def make(*, mode="live", events=("failed", "on_hold", "flagged"), channel=CHANNEL, token=TOKEN):
        write_config(
            f"[notifications]\nmode={json.dumps(mode)}\nslack_channel={json.dumps(channel)}\n"
            f'events={json.dumps(list(events))}\n[web]\nbase_url="https://dashboard.example/reel/"\n'
        )
        env = {"LIBRARY_API_TOKEN": OTHER_TOKEN}
        if token is not None:
            env["SLACK_BOT_TOKEN"] = token
        return load_config(project / "config" / "reel.toml", environ=env)

    return make


@pytest.fixture
def queue(db):
    return JobQueue(db, policy=RetryPolicy(max_attempts=1), clock=lambda: NOW)


def new_job(db, queue, *, stage=Stage.IMPORT, group="Python 12", lesson=7):
    with db.transaction() as session:
        recording = Recording(
            source_path=r"C:\private\lessons\Դաս 7.mp4",
            size_bytes=1,
            group_name=group,
            lesson_number=lesson,
        )
        session.add(recording)
        session.flush()
        recording_id = recording.id
    return queue.enqueue(recording_id, stage=stage).id


def fail(db, queue, *, stage=Stage.IMPORT, error="Հայերեն սխալ", **kwargs):
    job_id = new_job(db, queue, stage=stage, **kwargs)
    claim = queue.claim(f"worker-{job_id}")
    assert claim.job_id == job_id
    queue.fail(claim, error, retryable=False)
    return job_id


def hold(db, queue, *, reason="consent_opted_out", message="Համաձայնություն չկա", **kwargs):
    job_id = new_job(db, queue, **kwargs)
    claim = queue.claim(f"worker-{job_id}")
    queue.hold(claim, reason, message)
    return job_id


def latest(db, job_id, kind):
    with db.transaction() as session:
        return session.scalar(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.kind == kind)
            .order_by(JobEvent.id.desc())
            .limit(1)
        ).id


def state(db, key=WATERMARK):
    with db.transaction() as session:
        row = session.get(AppMeta, key)
        return row.value if row is not None else None


def ready(db, configuration, client, **kwargs):
    cfg = configuration()
    notifier = StaffNotifier(db, cfg, client, clock=lambda: NOW, **kwargs)
    assert notifier.run_once() == 0
    return cfg, notifier


def test_first_run_skips_history_then_second_run_and_restart_do_not_repeat(
    db, queue, client, configuration
):
    old_job = fail(db, queue)
    old_event = latest(db, old_job, EventKind.FAILED)
    cfg = configuration()
    notifier = StaffNotifier(db, cfg, client)
    assert notifier.run_once() == 0 and int(state(db)) == old_event
    assert client.messages == client.histories == []
    new_id = fail(db, queue)
    new_event = latest(db, new_id, EventKind.FAILED)
    assert notifier.run_once() == 1 and int(state(db)) == new_event
    assert notifier.run_once() == StaffNotifier(db, cfg, client).run_once() == 0
    assert len(client.messages) == 1 and state(db, PENDING) is None


def test_failed_and_held_events_are_sent_in_id_order_with_stage_reason_attempt_and_link(
    db, queue, client, configuration, caplog
):
    caplog.set_level(logging.INFO, logger="reel.notify")
    _, notifier = ready(db, configuration, client)
    failed = fail(db, queue, stage=Stage.TRANSCRIBE, error="Աուդիոն չի բացվում")
    held = hold(db, queue, stage=Stage.CONSENT)
    queue.rerun(
        failed, "staff", Stage.IMPORT
    )  # Current job error/stage no longer describe failure.
    queue.release(held, "staff")  # Current hold reason/message have been cleared.
    assert notifier.run_once() == 2
    ids = [message["metadata"]["event_payload"]["event_id"] for message in client.messages]
    assert (
        ids
        == sorted(ids)
        == [latest(db, failed, EventKind.FAILED), latest(db, held, EventKind.HELD)]
    )
    first, second = [message["text"] for message in client.messages]
    assert "ձախողվել" in first and "Աուդիոն չի բացվում" in first
    assert "Transcribed" in first and "Փորձեր՝ 1" in first
    assert "Python 12 · Դաս 7" in first and f"https://dashboard.example/reel/jobs/{failed}" in first
    assert "Consent" in second and "consent_opted_out" in second and "Համաձայնություն չկա" in second
    assert len([record for record in caplog.records if record.name == "reel.notify"]) == 2
    assert all(
        record.notification_at == NOW.isoformat()
        for record in caplog.records
        if record.name == "reel.notify"
    )


@pytest.mark.parametrize("reason", ["consent_opted_out", "identification"])
def test_hold_reason_and_unknown_identity_use_filename_without_private_path(
    db, queue, client, configuration, reason
):
    _, notifier = ready(db, configuration, client)
    job_id = hold(db, queue, reason=reason, group=None, lesson=None)
    assert notifier.run_once() == 1
    text = client.messages[0]["text"]
    assert "Դաս 7.mp4" in text and "private" not in text and reason in text
    assert f"/jobs/{job_id}" in text


@pytest.mark.parametrize("stage", [Stage.IMPORT, Stage.TRANSCRIBE])
def test_completed_import_and_transcribe_with_flags_are_notified(
    db, queue, client, configuration, stage
):
    _, notifier = ready(db, configuration, client)
    job_id = new_job(db, queue, stage=stage)
    claim = queue.claim("worker")
    with db.transaction(immediate=True) as session:
        session.get(Job, job_id).flags = ["unknown_group", "empty_transcript"]
    queue.complete(claim)
    assert notifier.run_once() == 1
    assert "Նշումներ՝ unknown_group, empty_transcript" in client.messages[0]["text"]
    assert stage.label in client.messages[0]["text"]


@pytest.mark.parametrize(
    ("flags", "sent"),
    [(["youtube_not_unlisted", "chapters_not_generated"], 1), (["youtube_no_playlist"], 0)],
)
def test_after_the_upload_only_a_private_lock_is_notified(
    db, queue, client, configuration, flags, sent
):
    _, notifier = ready(db, configuration, client)
    job_id = new_job(db, queue, stage=Stage.UPLOAD)
    claim = queue.claim("worker")
    with db.transaction(immediate=True) as session:
        session.get(Job, job_id).flags = flags
    queue.complete(claim)
    assert notifier.run_once() == sent and len(client.messages) == sent
    if sent:
        assert "youtube_not_unlisted" in client.messages[0]["text"]


def test_a_job_over_its_time_budget_is_notified(db, queue, client, configuration):
    from reel.metrics import check_budget

    _, notifier = ready(db, configuration, client)
    job_id = new_job(db, queue, stage=Stage.CLEAN)
    now = utcnow()
    with db.transaction(immediate=True) as session:
        session.add(StageRun(job_id=job_id, stage=Stage.TRANSCRIBE, attempt=1,
                             status=StageRunStatus.SUCCEEDED, started_at=now - timedelta(hours=5),
                             finished_at=now))  # fmt: skip
    with db.transaction(immediate=True) as session:
        assert check_budget(session, job_id, 240) == 300
    assert notifier.run_once() == 1
    text = client.messages[0]["text"]
    assert "բյուջեն" in text and "300 min" in text and "budget_exceeded" in text


def test_event_time_flags_snapshot_wins_over_later_job_changes(db, queue, client, configuration):
    _, notifier = ready(db, configuration, client)
    job_id = new_job(db, queue)
    claim = queue.claim("worker")
    queue.complete(claim)
    event_id = latest(db, job_id, EventKind.STAGE_COMPLETED)
    with db.transaction(immediate=True) as session:
        event = session.get(JobEvent, event_id)
        event.data = {**event.data, "flags": ["unknown_group"]}
        session.get(Job, job_id).flags = []
    assert notifier.run_once() == 1 and "unknown_group" in client.messages[0]["text"]


def test_empty_event_flags_snapshot_does_not_report_later_flags(db, queue, client, configuration):
    _, notifier = ready(db, configuration, client)
    job_id = new_job(db, queue)
    queue.complete(queue.claim("worker"))
    with db.transaction(immediate=True) as session:
        event = session.get(JobEvent, latest(db, job_id, EventKind.STAGE_COMPLETED))
        event.data = {**event.data, "flags": []}
        session.get(Job, job_id).flags = ["added_after_completion"]
    assert notifier.run_once() == 0 and not client.messages


@pytest.mark.parametrize(
    "stage,flags", [(Stage.IMPORT, []), (Stage.CLEAN, ["review"]), (Stage.CHAPTERS, ["review"])]
)
def test_no_flag_alert_for_empty_flags_or_other_completed_stages(
    db, queue, client, configuration, stage, flags
):
    _, notifier = ready(db, configuration, client)
    job_id = new_job(db, queue, stage=stage)
    claim = queue.claim("worker")
    with db.transaction(immediate=True) as session:
        session.get(Job, job_id).flags = flags
    queue.complete(claim)
    assert notifier.run_once() == 0 and not client.messages
    assert int(state(db)) == latest(db, job_id, EventKind.STAGE_COMPLETED)


@pytest.mark.parametrize("events", [(), ("on_hold",), ("flagged",)])
def test_event_filter_skips_failed_events_and_advances_watermark(
    db, queue, client, configuration, events
):
    notifier = StaffNotifier(db, configuration(events=events), client)
    notifier.run_once()
    job_id = fail(db, queue)
    assert notifier.run_once() == 0 and not client.messages
    assert int(state(db)) == latest(db, job_id, EventKind.FAILED)


def test_retry_scheduled_is_not_a_failed_notification(db, client, configuration):
    queue = JobQueue(db, policy=RetryPolicy(max_attempts=2), clock=lambda: NOW)
    _, notifier = ready(db, configuration, client)
    new_job(db, queue)
    queue.fail(queue.claim("worker"), "temporary", retryable=True)
    assert notifier.run_once() == 0 and not client.messages


def test_dry_run_logs_armenian_and_updates_watermark_without_http(
    db, queue, client, configuration, caplog
):
    caplog.set_level(logging.INFO, logger="reel.notify")
    cfg = configuration(mode="dry-run", channel="", token=None)
    notifier = StaffNotifier(db, cfg, client)
    notifier.run_once()
    job_id = fail(db, queue)
    assert notifier.run_once() == 1 and "Հայերեն սխալ" in caplog.text and "dry-run" in caplog.text
    assert not client.messages and not client.histories and not client.attempts
    assert notifier.run_once() == 0 and int(state(db)) == latest(db, job_id, EventKind.FAILED)


def test_retryable_error_retains_pending_payload_and_retries_next_run(
    db, queue, client, configuration
):
    cfg, notifier = ready(db, configuration, client)
    job_id = fail(db, queue)
    event_id = latest(db, job_id, EventKind.FAILED)
    client.failures = [SlackError("ratelimited", retryable=True, retry_after=1)]
    assert notifier.run_once() == 0 and int(state(db)) < event_id
    pending = state(db, PENDING)
    assert json.loads(pending)["event_id"] == event_id
    with db.transaction(immediate=True) as session:
        session.get(Job, job_id).last_error = "different error after retry"
    assert StaffNotifier(db, cfg, client).run_once() == 1
    assert len(client.attempts) == 2 and client.attempts[0] == client.attempts[1]
    assert len(client.messages) == 1 and int(state(db)) == event_id and state(db, PENDING) is None


def test_permanent_error_is_skipped_logged_and_does_not_block_next_event(
    db, queue, client, configuration, caplog
):
    _, notifier = ready(db, configuration, client)
    first, second = fail(db, queue), fail(db, queue)
    client.failures = [SlackError("invalid_auth", retryable=False)]
    assert notifier.run_once() == 1
    assert [message["metadata"]["event_payload"]["job_id"] for message in client.messages] == [
        second
    ]
    assert "skipped" in caplog.text and "invalid_auth" in caplog.text
    assert str(latest(db, first, EventKind.FAILED)) in caplog.text
    assert notifier.run_once() == 0


def test_lost_post_response_is_recovered_on_restart_without_repeating_message(
    db, queue, client, configuration
):
    cfg, notifier = ready(db, configuration, client)
    job_id = fail(db, queue)
    client.lose_response = True
    assert notifier.run_once() == 0 and len(client.messages) == 1
    assert StaffNotifier(db, cfg, client).run_once() == 1
    assert len(client.messages) == len(client.attempts) == 1
    assert int(state(db)) == latest(db, job_id, EventKind.FAILED)


def test_crash_after_post_before_ack_is_recovered_from_metadata(
    db, queue, client, configuration, monkeypatch
):
    cfg, notifier = ready(db, configuration, client)
    fail(db, queue)

    def crash(alert):
        raise RuntimeError("simulated crash before watermark commit")

    monkeypatch.setattr(notifier, "_ack", crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        notifier.run_once()
    assert len(client.messages) == 1 and state(db, PENDING)
    assert StaffNotifier(db, cfg, client).run_once() == 1
    assert len(client.messages) == len(client.attempts) == 1


def test_slack_io_is_outside_database_writer_transaction(db, queue, client, configuration):
    _, notifier = ready(db, configuration, client)
    job_id = fail(db, queue)

    def write_during_post():
        with db.transaction(immediate=True) as session:
            session.get(Job, job_id).flags = ["other writer was not blocked"]

    client.on_post = write_during_post
    assert notifier.run_once() == 1


def test_prepared_flag_notification_survives_flags_being_cleared_during_retry(
    db, queue, client, configuration
):
    cfg, notifier = ready(db, configuration, client)
    job_id = new_job(db, queue)
    claim = queue.claim("worker")
    with db.transaction(immediate=True) as session:
        session.get(Job, job_id).flags = ["unknown_group"]
    queue.complete(claim)
    client.failures = [SlackError("connection_error", retryable=True)]
    assert notifier.run_once() == 0
    with db.transaction(immediate=True) as session:
        session.get(Job, job_id).flags = []
    assert StaffNotifier(db, cfg, client).run_once() == 1
    assert "unknown_group" in client.messages[0]["text"]


def test_values_are_slack_escaped_and_all_known_secrets_are_redacted(
    db, queue, client, configuration, caplog
):
    caplog.set_level(logging.INFO, logger="reel.notify")
    _, notifier = ready(db, configuration, client)
    fail(db, queue, error=f"Խնդիր <@U123> & {TOKEN} {OTHER_TOKEN}", group="A&B <@U456>")
    caplog.clear()  # Queue logging is outside this notifier's component.
    assert notifier.run_once() == 1
    text = client.messages[0]["text"]
    assert "A&amp;B &lt;@U456&gt;" in text and "&lt;@U123&gt; &amp;" in text
    assert (
        "[REDACTED]" in text
        and TOKEN not in text + caplog.text
        and OTHER_TOKEN not in text + caplog.text
    )
    assert "Խնդիր".encode() in text.encode("utf-8")


def test_metadata_has_job_event_and_database_namespace(db, queue, client, configuration):
    _, notifier = ready(db, configuration, client)
    job_id = fail(db, queue)
    notifier.run_once()
    with db.transaction() as session:
        namespace = session.get(AppMeta, "instance_id").value
    assert client.messages[0]["metadata"] == {
        "event_type": "reel_staff_alert",
        "event_payload": {
            "job_id": job_id,
            "event_id": latest(db, job_id, EventKind.FAILED),
            "instance_id": namespace,
        },
    }


def test_recovery_does_not_confuse_another_database_instance(db, queue, client, configuration):
    _, notifier = ready(db, configuration, client)
    job_id = fail(db, queue)
    client.messages = [
        {
            "ts": "1700000000.000001",
            "metadata": {
                "event_type": "reel_staff_alert",
                "event_payload": {
                    "job_id": job_id,
                    "event_id": latest(db, job_id, EventKind.FAILED),
                    "instance_id": "other-db",
                },
            },
        }
    ]
    assert notifier.run_once() == 1 and len(client.messages) == 2


def test_recovery_follows_history_cursor_before_new_post(db, queue, client, configuration):
    cfg, notifier = ready(db, configuration, client)
    fail(db, queue)
    client.lose_response = True
    notifier.run_once()
    client.pages = [
        {
            "ok": True,
            "messages": [],
            "has_more": True,
            "response_metadata": {"next_cursor": "page-2"},
        },
        {"ok": True, "messages": deepcopy(client.messages), "has_more": False},
    ]
    assert StaffNotifier(db, cfg, client).run_once() == 1
    assert client.histories[-1][1] == "page-2" and len(client.attempts) == 1


@pytest.mark.parametrize(
    "page",
    [
        {"messages": "bad"},
        {"messages": [], "has_more": True},
        {"messages": [17]},
        {"messages": [], "response_metadata": {"next_cursor": 2}},
    ],
)
def test_unverifiable_history_never_posts_or_acknowledges(db, queue, client, configuration, page):
    _, notifier = ready(db, configuration, client)
    job_id = fail(db, queue)
    client.pages = [page]
    assert notifier.run_once() == 0 and not client.attempts
    assert int(state(db)) < latest(db, job_id, EventKind.FAILED)


def test_two_notifiers_in_one_process_do_not_race_to_post(db, queue, client, configuration):
    cfg, notifier = ready(db, configuration, client)
    fail(db, queue)
    other = StaffNotifier(db, cfg, client)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda instance: instance.run_once(), [notifier, other]))
    assert sorted(results) == [0, 1] and len(client.messages) == 1


@pytest.mark.parametrize("token", [None, "", "bad token"])
def test_live_requires_token_before_processing_events(db, client, configuration, token):
    with pytest.raises(SlackError, match="SLACK_BOT_TOKEN") as error:
        StaffNotifier(db, configuration(token=token), client)
    assert not error.value.retryable and not client.histories and state(db) is None


def test_live_requires_channel_id_not_name(db, client, configuration):
    with pytest.raises(SlackError, match="channel ID") as error:
        StaffNotifier(db, configuration(channel="#staff"), client)
    assert not error.value.retryable and not client.histories and state(db) is None


def test_invalid_persisted_watermark_is_not_silently_reset(db, client, configuration):
    _, notifier = ready(db, configuration, client)
    with db.transaction(immediate=True) as session:
        session.get(AppMeta, WATERMARK).value = "corrupt"
    with pytest.raises(RuntimeError, match="last_event_id"):
        notifier.run_once()
    assert not client.attempts


def test_poll_interval_is_injectable_and_stop_wait_is_used(db, client, configuration):
    notifier = StaffNotifier(
        db, configuration(mode="dry-run", channel=""), client, poll_interval=0.02
    )

    class Stop:
        stopped = False
        intervals = []

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            self.intervals.append(seconds)
            self.stopped = True

    stop = Stop()
    notifier.run(stop)
    assert stop.intervals == [0.02]
    assert StaffNotifier(db, configuration(mode="dry-run", channel=""), client).poll_interval == 30


def test_loop_stops_promptly_and_delivers_committed_queue_event(db, queue, client, configuration):
    _, notifier = ready(db, configuration, client, poll_interval=0.01)
    stop = threading.Event()
    thread = threading.Thread(target=notifier.run, args=(stop,))
    thread.start()
    try:
        fail(db, queue)
        assert client.sent.wait(3)
    finally:
        stop.set()
        thread.join(3)
    assert not thread.is_alive() and len(client.messages) == 1


def test_loop_logs_only_exception_type_and_keeps_running_until_stop(
    db, client, configuration, monkeypatch, caplog
):
    notifier = StaffNotifier(
        db, configuration(mode="dry-run", channel=""), client, poll_interval=0.01
    )
    stop = threading.Event()

    def fail_once():
        stop.set()
        raise RuntimeError(TOKEN)

    monkeypatch.setattr(notifier, "run_once", fail_once)
    notifier.run(stop)
    assert "RuntimeError" in caplog.text and TOKEN not in caplog.text


def test_switching_to_dry_run_never_retries_a_pending_live_alert_over_http(
    db, queue, client, configuration, caplog
):
    caplog.set_level(logging.INFO, logger="reel.notify")
    _, notifier = ready(db, configuration, client)
    job_id = fail(db, queue)
    client.failures = [SlackError("connection_error", retryable=True)]
    assert notifier.run_once() == 0 and state(db, PENDING)
    calls = len(client.histories), len(client.attempts)
    dry = StaffNotifier(db, configuration(mode="dry-run", channel="", token=None), client)
    assert dry.run_once() == 1
    assert (len(client.histories), len(client.attempts)) == calls
    assert "dry-run" in caplog.text and not client.messages
    assert state(db, PENDING) is None and int(state(db)) == latest(db, job_id, EventKind.FAILED)


def test_disabling_an_event_type_skips_its_pending_retry_without_http(
    db, queue, client, configuration
):
    _, notifier = ready(db, configuration, client)
    job_id = fail(db, queue)
    client.failures = [SlackError("connection_error", retryable=True)]
    assert notifier.run_once() == 0
    calls = len(client.histories), len(client.attempts)
    disabled = StaffNotifier(db, configuration(events=()), client)
    assert disabled.run_once() == 0
    assert (len(client.histories), len(client.attempts)) == calls
    assert state(db, PENDING) is None and int(state(db)) == latest(db, job_id, EventKind.FAILED)


def test_pending_dry_run_is_not_turned_into_a_real_post_after_restart(
    db, queue, client, configuration, monkeypatch
):
    dry = StaffNotifier(db, configuration(mode="dry-run", channel="", token=None), client)
    dry.run_once()
    fail(db, queue)

    def crash(alert):
        raise RuntimeError("crash after dry-run logging")

    monkeypatch.setattr(dry, "_ack", crash)
    with pytest.raises(RuntimeError):
        dry.run_once()
    assert state(db, PENDING)
    live = StaffNotifier(db, configuration(), client)
    assert live.run_once() == 1
    assert not client.histories and not client.attempts and not client.messages
