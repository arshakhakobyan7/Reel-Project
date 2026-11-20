"""Stages ``upload`` and ``post`` with fake YouTube, Slack and Classroom services (T-706)."""

from datetime import UTC, date, datetime

import pytest
from classroom_fake import ClassroomFake
from sqlalchemy import func, select
from youtube_fake import YoutubeFake

from reel.auth.google import REVOKED, AuthError
from reel.config import load_config
from reel.db import (
    Job,
    JobEvent,
    JobStatus,
    LibraryEntry,
    PublicationTarget,
    Recording,
    Stage,
    VideoMetadata,
    init_database,
    open_database,
)
from reel.db.enums import EventKind, PublicationStatus, TargetKind
from reel.publish.classroom import ClassroomPublisher
from reel.publish.slack import SlackError, SlackPublisher
from reel.publish.youtube import CHUNK_UNIT, YoutubePublisher
from reel.queue import JobQueue, RetryPolicy, StageRegistry, Worker, create_job
from reel.queue.stage import StageCancelled, StageContext, StageFailed
from reel.stages.cleaning import CLEANED_FILE
from reel.stages.publishing import (
    PREVIEW_URL,
    PostStage,
    UploadStage,
    _save_state,
    seconds_until_quota_reset,
)

WORKERS = """
[workers]
max_attempts = 2
backoff_base_seconds = 0.01
backoff_factor = 1.0
idle_poll_seconds = 0.02
"""
LIVE = '\n[youtube]\nmode = "live"\n[slack]\nmode = "live"\n[classroom]\nmode = "live"\n'
GROUPS = """
[groups."Python 12"]
youtube_playlist_id = "PL_GROUP"
slack_channel = "C0123456789"
classroom_course_id = "course-12"
"""
TITLE = "Python 12 · Դաս 14 · Սեփական ֆունկցիաներ"
CHAPTERS = [{"start": 0.0, "title": "Ներածություն"}, {"start": 95.0, "title": "Ֆունկցիաներ"}]


class Tokens:
    def token(self):
        return "fake-access-token"

    def invalidate(self):
        pass


class Codec:
    """In-memory stand-in for the DPAPI session codec, shared by all attempts."""

    def __init__(self):
        self.values = {}

    def seal(self, value):
        key = f"sealed-{len(self.values)}"
        self.values[key] = value
        return key

    def unseal(self, key):
        return self.values[key]


class SlackFake:
    """The Slack Web API methods the publisher uses; messages keep their metadata."""

    def __init__(self):
        self.messages, self.calls, self.fail_posts = [], [], []
        self.lose_response = False

    def history(self, channel, *, cursor="", limit=100):
        messages = [dict(m) for m in reversed(self.messages) if m["channel"] == channel]
        return {"ok": True, "messages": messages, "has_more": False,
                "response_metadata": {"next_cursor": ""}}  # fmt: skip

    def post_message(self, channel, text, metadata):
        self.calls.append("post")
        if self.fail_posts:
            raise self.fail_posts.pop(0)
        ts = f"1700000000.{len(self.messages) + 1:06d}"
        self.messages.append({"channel": channel, "ts": ts, "text": text, "metadata": metadata})
        if self.lose_response:  # the message exists, but its answer is lost
            self.lose_response = False
            raise SlackError("transport_error", retryable=True)
        return {"ok": True, "channel": channel, "ts": ts}

    def update_message(self, channel, ts, text, metadata):
        self.calls.append("update")
        message = next(m for m in self.messages if m["ts"] == ts and m["channel"] == channel)
        message.update(text=text, metadata=metadata)
        return {"ok": True, "channel": channel, "ts": ts}

    def get_permalink(self, channel, ts):
        return f"https://demo.slack.com/archives/{channel}/p{ts.replace('.', '')}"


@pytest.fixture
def services():
    with YoutubeFake() as youtube, ClassroomFake() as classroom:
        yield youtube, SlackFake(), classroom


@pytest.fixture
def setup(project, write_config):
    opened = []

    def setup(*, extra="", groups=GROUPS):
        write_config(WORKERS + extra)
        write_config(groups, "groups.toml")
        cfg = load_config(project / "config" / "reel.toml")
        init_database(cfg.paths.database_file)
        db = open_database(cfg.paths.database_file)
        opened.append(db)
        with db.transaction() as s:
            recording = Recording(
                source_path="lesson.mp4", size_bytes=1, group_name="Python 12",
                lesson_number=14, recording_date=date(2026, 11, 19),
            )  # fmt: skip
            s.add(recording)
            job_id = create_job(s, recording, stage=Stage.UPLOAD).id
            s.add(VideoMetadata(job_id=job_id, title=TITLE, summary="ֆունկցիաներ, return",
                                description="Նկարագրություն\n0:00 Ներածություն",
                                chapters=CHAPTERS))  # fmt: skip
        job_dir = cfg.paths.data_dir / "jobs" / str(job_id)
        job_dir.mkdir(parents=True)
        (job_dir / CLEANED_FILE).write_bytes(bytes(range(256)) * (2 * CHUNK_UNIT // 256) + b"end")
        return cfg, db, job_id

    yield setup
    for db in opened:
        db.dispose()


def stages(services, *, codec=None, tokens=Tokens):
    youtube, slack, classroom = services
    codec = codec or Codec()

    def make_youtube(settings, access):
        return YoutubePublisher(
            settings, access, api_url=youtube.url + "/api/", upload_url=youtube.url + "/upload/",
            chunk_size=CHUNK_UNIT, sleep=lambda seconds: None, max_retries=0,
            session_codec=codec,
        )  # fmt: skip

    def make_classroom(settings, access):
        return ClassroomPublisher(settings, access, api_url=classroom.url)

    return StageRegistry(
        [
            UploadStage(make_youtube, tokens_factory=lambda cfg: tokens()),
            PostStage(
                slack_factory=lambda settings, token: SlackPublisher(settings, client=slack),
                classroom_factory=make_classroom,
                tokens_factory=lambda cfg: tokens(),
            ),
        ]
    )


def work(cfg, db, registry):
    attention: list = []
    worker = Worker(db, registry, lambda: cfg, notifier=attention.append)
    worker.start(until_idle=True)
    assert worker.join(60)
    return attention


def job(db, job_id):
    with db.transaction() as s:
        row = s.get(Job, job_id)
        return row.status, row.stage, list(row.flags), row.last_error, row.publication_summary()


def rows(db, job_id):
    with db.transaction() as s:
        return {
            r.target: {
                "status": r.status, "dry_run": r.dry_run, "external_id": r.external_id,
                "url": r.url, "attempts": r.attempts, "error": r.last_error,
                "details": dict(r.details),
            }
            for r in s.scalars(select(PublicationTarget).where(PublicationTarget.job_id == job_id))
        }  # fmt: skip


def library_entries(db):
    with db.transaction() as s:
        return [(e.title, e.video_url, len(e.chapters)) for e in s.scalars(select(LibraryEntry))]


def test_dry_run_goes_through_to_done_without_any_call(setup):
    cfg, db, job_id = setup()
    work(cfg, db, StageRegistry([UploadStage(), PostStage()]))  # the real publishers
    status, _, flags, _, summary = job(db, job_id)
    assert status is JobStatus.DONE and summary["youtube_url"] is None
    found = rows(db, job_id)
    assert all(r["status"] is PublicationStatus.PUBLISHED and r["dry_run"] for r in found.values())
    assert all(r["external_id"] is None and r["url"] is None for r in found.values())
    plan = found[TargetKind.YOUTUBE]["details"]
    assert plan["video_action"]["action"] == "upload" and plan["playlist_id"] == "PL_GROUP"
    assert plan["metadata"]["status"]["privacyStatus"] == "unlisted"
    assert plan["metadata"]["snippet"]["title"] == TITLE
    text = found[TargetKind.SLACK]["details"]["text"]
    assert PREVIEW_URL in text and TITLE in text
    assert found[TargetKind.CLASSROOM]["details"]["body"]["materials"][0]["link"]["url"] == (
        PREVIEW_URL
    )
    assert found[TargetKind.LIBRARY]["details"]["payload"]["chapters"][1]["start_seconds"] == 95
    assert library_entries(db) == []  # a preview writes no catalogue entry
    assert "youtube_no_playlist" not in flags


def test_live_publishing_then_reruns_update_without_duplicates(
    setup, services, project, write_config
):
    youtube, slack, classroom = services
    cfg, db, job_id = setup(extra=LIVE)
    registry = stages(services)
    work(cfg, db, registry)
    status, _, _, _, summary = job(db, job_id)
    assert status is JobStatus.DONE
    url = summary["youtube_url"]
    assert url == f"https://www.youtube.com/watch?v={summary['youtube_video_id']}"
    assert len(youtube.videos) == 1 and youtube.count("POST", "/upload/videos") == 1
    assert len(slack.messages) == 1 and url in slack.messages[0]["text"]
    assert summary["slack_message_link"].startswith("https://demo.slack.com/archives/C0123456789/")
    assert [m["link"]["url"] for p in classroom.active() for m in p["materials"]] == [url]
    assert summary["classroom_post_id"] and summary["library_entry_id"]
    assert library_entries(db) == [(TITLE, url, 2)]
    found = rows(db, job_id)
    assert all(not r["dry_run"] and r["attempts"] == 1 for r in found.values())

    # Staff change the title and run the upload again: everything is updated in place.
    with db.transaction(immediate=True) as s:
        s.scalar(select(VideoMetadata)).title = "Նոր վերնագիր"
    JobQueue(db).rerun(job_id, "aram", Stage.UPLOAD)
    work(cfg, db, registry)
    assert job(db, job_id)[0] is JobStatus.DONE
    assert len(youtube.videos) == 1 and youtube.count("POST", "/upload/videos") == 1
    assert next(iter(youtube.videos.values()))["snippet"]["title"] == "Նոր վերնագիր"
    assert len(slack.messages) == 1 and "Նոր վերնագիր" in slack.messages[0]["text"]
    assert slack.calls == ["post", "update"]
    assert len(classroom.active()) == 1 and "Նոր վերնագիր" in classroom.active()[0]["text"]
    assert library_entries(db) == [("Նոր վերնագիր", url, 2)]

    # The same inputs again: the posts are left alone.
    JobQueue(db).rerun(job_id, "aram", Stage.POST)
    work(cfg, db, registry)
    assert slack.calls == ["post", "update"] and classroom.count("POST") == 1

    # YouTube back in dry-run: the real video record is kept, the preview stored beside it.
    write_config(WORKERS + '[slack]\nmode = "live"\n[classroom]\nmode = "live"\n')
    dry_cfg = load_config(project / "config" / "reel.toml")
    assert dry_cfg.settings.youtube.mode == "dry-run"
    JobQueue(db).rerun(job_id, "aram", Stage.UPLOAD)
    work(dry_cfg, db, registry)
    record = rows(db, job_id)[TargetKind.YOUTUBE]
    assert job(db, job_id)[0] is JobStatus.DONE and record["url"] == url
    assert not record["dry_run"] and record["details"]["preview"]["dry_run"] is True
    assert record["details"]["complete"] is True


def test_a_failing_target_is_retried_alone_while_the_others_complete(setup, services):
    youtube, slack, classroom = services
    slack.fail_posts = [SlackError("ratelimited", retryable=True, retry_after=0.01)]
    cfg, db, job_id = setup(extra=LIVE)
    work(cfg, db, stages(services))
    assert job(db, job_id)[0] is JobStatus.DONE
    found = rows(db, job_id)
    assert found[TargetKind.SLACK]["attempts"] == 2 and found[TargetKind.SLACK]["error"] is None
    assert found[TargetKind.CLASSROOM]["attempts"] == 1 and classroom.count("POST") == 1
    assert found[TargetKind.LIBRARY]["attempts"] == 1 and len(library_entries(db)) == 1
    assert len(slack.messages) == 1


def test_a_target_out_of_retries_fails_the_job_with_the_partial_publication(setup, services):
    youtube, slack, classroom = services
    slack.fail_posts = [SlackError("ratelimited", retryable=True)] * 5
    cfg, db, job_id = setup(extra=LIVE)
    attention = work(cfg, db, stages(services))
    status, stage, _, error, summary = job(db, job_id)
    assert status is JobStatus.FAILED and stage is Stage.POST
    assert error.startswith("Slack: Slack request failed: ratelimited.")
    assert "Classroom: published" in error and "Library: published" in error  # earlier
    assert [a.kind for a in attention] == ["failed"]
    found = rows(db, job_id)
    assert found[TargetKind.SLACK]["status"] is PublicationStatus.FAILED
    assert found[TargetKind.SLACK]["attempts"] == 2 and not slack.messages
    assert found[TargetKind.CLASSROOM]["status"] is PublicationStatus.PUBLISHED
    assert summary["youtube_url"] and summary["classroom_post_id"] and summary["library_entry_id"]


def test_a_lost_slack_answer_is_found_again_not_posted_twice(setup, services):
    youtube, slack, classroom = services
    slack.lose_response = True
    cfg, db, job_id = setup(extra=LIVE)
    work(cfg, db, stages(services))
    assert job(db, job_id)[0] is JobStatus.DONE
    assert len(slack.messages) == 1 and slack.calls == ["post", "update"]
    assert rows(db, job_id)[TargetKind.SLACK]["external_id"] == "C0123456789:1700000000.000001"


def test_an_interrupted_upload_resumes_the_same_session(setup, services):
    youtube, slack, classroom = services
    youtube.transfer_faults = ["503"]
    cfg, db, job_id = setup(extra=LIVE)
    work(cfg, db, stages(services))
    assert job(db, job_id)[0] is JobStatus.DONE
    assert youtube.count("POST", "/upload/videos") == 1 and len(youtube.videos) == 1
    record = rows(db, job_id)[TargetKind.YOUTUBE]
    assert record["attempts"] == 2 and record["details"]["complete"] is True
    with db.transaction() as s:
        events = s.scalars(select(JobEvent.kind).where(JobEvent.job_id == job_id)).all()
    assert EventKind.RETRY_SCHEDULED in events


def test_a_used_up_quota_waits_until_it_resets(setup, services):
    youtube, slack, classroom = services
    youtube.failures[("GET", "/api/channels")] = [(403, "quotaExceeded", "quota", {})] * 5
    cfg, db, job_id = setup(extra=LIVE)
    queue = JobQueue(db, policy=RetryPolicy.from_settings(cfg.settings.workers))
    claim = queue.claim("worker-1")
    ctx = StageContext(claim, cfg, db, cfg.paths.data_dir / "jobs" / str(job_id))
    with pytest.raises(StageFailed) as failure:
        stages(services).get(Stage.UPLOAD).run(ctx)
    retry_after = failure.value.retry_after
    assert failure.value.retryable and abs(retry_after - seconds_until_quota_reset()) < 60
    assert rows(db, job_id)[TargetKind.YOUTUBE]["status"] is PublicationStatus.FAILED
    queue.fail(claim, str(failure.value), retry_after=retry_after)  # as the worker does
    with db.transaction() as s:
        row = s.get(Job, job_id)
        status, wait = row.status, (row.next_attempt_at - row.updated_at).total_seconds()
    assert status is JobStatus.QUEUED and abs(wait - retry_after) < 1


@pytest.mark.parametrize(
    ("now", "seconds"),
    [
        (datetime(2026, 11, 20, 7, 0, tzinfo=UTC), 3600 + 600),  # 23:00 in Los Angeles
        (datetime(2026, 3, 8, 8, 30, tzinfo=UTC), 22.5 * 3600 + 600),  # the day DST starts
    ],
)
def test_quota_reset_time(now, seconds):
    assert seconds_until_quota_reset(now) == seconds


def test_revoked_google_access_fails_at_once_with_a_clear_message(setup, services):
    def revoked():
        raise AuthError(REVOKED, reason="revoked")

    cfg, db, job_id = setup(extra=LIVE)
    attention = work(cfg, db, stages(services, tokens=revoked))
    status, stage, _, error, _ = job(db, job_id)
    assert status is JobStatus.FAILED and stage is Stage.UPLOAD
    assert "reel auth google" in error and [a.kind for a in attention] == ["failed"]
    assert rows(db, job_id)[TargetKind.YOUTUBE]["attempts"] == 1


def test_targets_without_a_place_are_skipped(setup, services):
    youtube, slack, classroom = services
    cfg, db, job_id = setup(extra=LIVE, groups='[groups."Python 12"]\n')
    work(cfg, db, stages(services))
    assert job(db, job_id)[0] is JobStatus.DONE
    found = rows(db, job_id)
    assert found[TargetKind.SLACK]["status"] is PublicationStatus.SKIPPED
    assert found[TargetKind.CLASSROOM]["details"]["skipped"].startswith("no Classroom course")
    assert not slack.calls and not classroom.calls and len(library_entries(db)) == 1
    assert "youtube_no_playlist" in job(db, job_id)[2]  # no playlist set for the group


def test_a_video_locked_to_private_is_flagged(setup, services):
    youtube, slack, classroom = services
    youtube.upload_status = {"uploadStatus": "processed", "privacyStatus": "private"}
    cfg, db, job_id = setup(extra=LIVE)
    work(cfg, db, stages(services))
    assert "youtube_not_unlisted" in job(db, job_id)[2]
    with db.transaction() as s:
        event = s.scalar(
            select(JobEvent).where(JobEvent.kind == EventKind.STAGE_COMPLETED,
                                   JobEvent.stage == Stage.UPLOAD)
        )  # fmt: skip
        assert "youtube_not_unlisted" in event.data["flags"]


def test_an_attempt_that_lost_the_job_cannot_write(setup):
    cfg, db, job_id = setup()
    queue = JobQueue(db)
    claim = queue.claim("worker-1")
    ctx = StageContext(claim, cfg, db, cfg.paths.data_dir)
    _save_state(ctx, TargetKind.YOUTUBE, {"dry_run": True, "step": 1})
    queue.cancel(job_id, "aram")
    with pytest.raises(StageCancelled):
        _save_state(ctx, TargetKind.YOUTUBE, {"dry_run": True, "step": 2})
    assert rows(db, job_id)[TargetKind.YOUTUBE]["details"]["step"] == 1
    with db.transaction() as s:
        assert s.scalar(select(func.count()).select_from(PublicationTarget)) == 1
