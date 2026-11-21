"""The recording detail page and the staff actions (T-804)."""

import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_web_dashboard import add_job

from reel.config import load_config
from reel.config.loader import load_groups
from reel.db import (
    ConsentCheck,
    Job,
    JobEvent,
    JobStatus,
    PublicationTarget,
    Recording,
    StaffUser,
    Stage,
    StageRun,
    Transcript,
    VideoMetadata,
    init_database,
    open_database,
    utcnow,
)
from reel.db.enums import (
    ConsentResult,
    PublicationStatus,
    StageRunStatus,
    TargetKind,
    TranscriptKind,
)
from reel.ingest.identify import FLAGS
from reel.queue.jobs import IDENTIFICATION_FLAGS
from reel.web import create_app, texts
from reel.web.security import LoginThrottle, hash_password

SECRET = "detail-test-secret-" + "q" * 30
PASSWORD = "detail-password-1"
TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def site(project, write_config):
    write_config("")
    write_config('[groups."Python 12"]\n[groups."Java 3"]\n', "groups.toml")
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    with db.transaction() as s:
        s.add(StaffUser(username="anna", password_hash=hash_password(PASSWORD), role="admin"))
    app = create_app(db, lambda: cfg, SECRET, throttle=LoginThrottle())
    with TestClient(app, follow_redirects=False) as client:
        token = TOKEN.search(client.get("/login").text)[1]
        data = {"username": "anna", "password": PASSWORD, "csrf_token": token}
        assert client.post("/login", data=data).status_code == 303
        yield SimpleNamespace(client=client, db=db, cfg=cfg, token=token)
    db.dispose()


def post(site, job_id, action, **form):
    token = TOKEN.search(site.client.get(f"/recordings/{job_id}").text)[1]
    return site.client.post(f"/recordings/{job_id}/{action}", data={"csrf_token": token, **form})


def job(db, job_id):
    with db.transaction() as s:
        row = s.get(Job, job_id)
        return SimpleNamespace(
            status=row.status, stage=row.stage, flags=list(row.flags),
            next_attempt_at=row.next_attempt_at, group=row.recording.group_name,
            lesson=row.recording.lesson_number, date=row.recording.recording_date,
        )  # fmt: skip


def events(db, job_id):
    with db.transaction() as s:
        return [
            (e.kind, e.actor_name, e.data.get("note"))
            for e in s.scalars(
                select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            )
        ]  # fmt: skip


def full_job(site):
    """A published lesson with every kind of record the page shows."""
    db = site.db
    job_id = add_job(db, group="Python 12", lesson=14, status=JobStatus.DONE, stage=Stage.POST,
                     original=9672.0)  # fmt: skip
    thumb = site.cfg.paths.data_dir / "jobs" / str(job_id) / "thumbnail.jpg"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
    now = utcnow()
    with db.transaction() as s:
        from reel.db import CutList

        s.add(CutList(job_id=job_id, version=2, original_duration_seconds=9672.0,
                      final_duration_seconds=6723.0, segments=[
            {"start": 0.0, "end": 600.0, "action": "remove", "reason": "before the lesson"},
            {"start": 600.0, "end": 5000.0, "action": "keep", "reason": "lesson"},
            {"start": 5000.0, "end": 5400.0, "action": "remove", "reason": "silence"},
            {"start": 5400.0, "end": 7723.0, "action": "keep", "reason": "lesson"},
            {"start": 7723.0, "end": 9672.0, "action": "remove",
             "reason": "nobody at the board for 1949 s (D-040)"},
        ]))  # fmt: skip
        s.add(Transcript(job_id=job_id, kind=TranscriptKind.CLEAN, language="hy", text="...",
                         segments=[{"start": 0.0, "end": 4.0, "text": "Բարև ձեզ"},
                                   {"start": 65.0, "end": 70.0,
                                    "text": "<i>ցուցակներ</i>"}]))  # fmt: skip
        s.add(VideoMetadata(job_id=job_id, title="Python 12 · Դաս 14 · Բառարաններ",
                            summary="Բառարաններ, live coding", description="Նկարագրություն\n0:00",
                            chapters=[{"start": 0.0, "title": "Կրկնություն"},
                                      {"start": 1120.0, "title": "Բառարաններ"}],
                            thumbnail_path=str(thumb)))  # fmt: skip
        s.add(ConsentCheck(job_id=job_id, provider="file", group_ref="core-7",
                           students=[{"id": "s1", "status": "granted"},
                                     {"id": "s2", "status": "denied"}],
                           result=ConsentResult.OPTED_OUT, message="s2 opted out",
                           released_by="anna", released_at=now,
                           release_note="Ուսանողը տեսախցիկում չէ"))  # fmt: skip
        s.add(StageRun(job_id=job_id, stage=Stage.TRANSCRIBE, attempt=1,
                       status=StageRunStatus.FAILED, started_at=now - timedelta(hours=2),
                       finished_at=now - timedelta(hours=1), error="whisper crashed"))  # fmt: skip
        s.add(StageRun(job_id=job_id, stage=Stage.TRANSCRIBE, attempt=2,
                       status=StageRunStatus.SUCCEEDED, started_at=now - timedelta(minutes=50),
                       finished_at=now - timedelta(minutes=10)))  # fmt: skip
        targets = [
            (TargetKind.YOUTUBE, PublicationStatus.PUBLISHED,
             "https://www.youtube.com/watch?v=abc", None, {}),
            (TargetKind.SLACK, PublicationStatus.FAILED, "javascript:alert(1)",
             "Slack request failed: ratelimited.", {}),
            (TargetKind.CLASSROOM, PublicationStatus.SKIPPED, None, None,
             {"skipped": "no Classroom course is set for the group"}),
        ]  # fmt: skip
        for target, status, url, error, details in targets:
            s.add(PublicationTarget(job_id=job_id, target=target, status=status, url=url,
                                    last_error=error, details=details, attempts=1))  # fmt: skip
        s.add(PublicationTarget(job_id=job_id, target=TargetKind.LIBRARY, dry_run=True,
                                status=PublicationStatus.PUBLISHED, details={}))  # fmt: skip
    return job_id


def test_the_detail_page_shows_the_whole_story(site):
    job_id = full_job(site)
    page = site.client.get(f"/recordings/{job_id}")
    assert page.status_code == 200
    html = page.text
    assert "<h1>Python 12 · Դաս 14</h1>" in html and html.count("pill-done") == 7
    assert "Ձայնագրություն՝ 2:41:12, կրճատված մինչև 1:52:03" in html
    assert html.count("<rect ") == 5 and html.count('class="removed"') == 3
    assert "Դասից առաջ՝ 10:00" in html and "Լռություն՝ 6:40" in html
    assert "Գրատախտակի մոտ ոչ ոք չկա՝ 32:29" in html
    assert "18:40</span> Բառարաններ" in html and "Python 12 · Դաս 14 · Բառարաններ" in html
    assert f'src="/recordings/{job_id}/thumbnail"' in html
    # the transcript is not shown any more (the user, 2026-11-20, D-061)
    assert "Բարև ձեզ" not in html and "ցուցակներ" not in html
    assert 'class="transcript"' not in html and "մաքրված տեսանյութի ժամանակներով" not in html
    assert 'href="https://www.youtube.com/watch?v=abc"' in html
    assert "javascript:" not in html  # an unsafe link is not shown as a link
    assert "Slack request failed: ratelimited." in html and "Բաց թողնված" in html
    assert "dry-run (նախադիտում)" in html
    assert "Կա չհամաձայնվող ուսանող" in html and "1 / 1 / 0" in html
    assert "Ուսանողը տեսախցիկում չէ" in html
    assert "whisper crashed" in html and "Հաջողվեց" in html and "Ձախողվեց" in html
    assert "Ստեղծվեց" in html  # the event history
    image = site.client.get(f"/recordings/{job_id}/thumbnail")
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert image.content == b"\xff\xd8fake-jpeg\xff\xd9"


def test_a_thumbnail_outside_the_data_folder_is_not_served(site, tmp_path):
    job_id = add_job(site.db, group="Python 12", lesson=1, status=JobStatus.DONE, stage=Stage.POST)
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"not for the web")
    with site.db.transaction() as s:
        s.add(VideoMetadata(job_id=job_id, title="t", thumbnail_path=str(outside)))
    assert site.client.get(f"/recordings/{job_id}/thumbnail").status_code == 404


def consent_hold(site):
    job_id = add_job(site.db, group="Python 12", lesson=3, status=JobStatus.ON_HOLD,
                     stage=Stage.CONSENT, hold_reason="consent_opted_out",
                     hold_message="A student did not consent")  # fmt: skip
    with site.db.transaction() as s:
        s.add(ConsentCheck(job_id=job_id, provider="file", students=[],
                           result=ConsentResult.OPTED_OUT))  # fmt: skip
    return job_id


def test_staff_accept_a_consent_hold_and_it_is_audited(site):
    job_id = consent_hold(site)
    html = site.client.get(f"/recordings/{job_id}").text
    assert "Ուսանողը համաձայն չէ հրապարակմանը" in html and "Ընդունել և շարունակել" in html
    response = post(site, job_id, "release", advance="1", note="Ստուգեցի, խնդիր չկա")
    assert response.status_code == 303
    assert response.headers["location"] == f"/recordings/{job_id}?done=accepted"
    assert texts.DONE_MESSAGES["accepted"] in site.client.get(response.headers["location"]).text
    state = job(site.db, job_id)
    assert state.status is JobStatus.QUEUED and state.stage is Stage.UPLOAD
    with site.db.transaction() as s:
        check = s.scalar(select(ConsentCheck).where(ConsentCheck.job_id == job_id))
        assert check.released_by == "anna" and check.release_note == "Ստուգեցի, խնդիր չկա"
    assert events(site.db, job_id)[-1] == ("released", "anna", "Ստուգեցի, խնդիր չկա")


def test_staff_run_a_held_stage_again(site):
    job_id = consent_hold(site)
    assert post(site, job_id, "release", advance="0").status_code == 303
    state = job(site.db, job_id)
    assert state.status is JobStatus.QUEUED and state.stage is Stage.CONSENT


def test_staff_cancel_a_job(site):
    job_id = add_job(site.db, status=JobStatus.FAILED, stage=Stage.TRANSCRIBE)
    assert post(site, job_id, "cancel", note="Կրկնօրինակ է").status_code == 303
    assert job(site.db, job_id).status is JobStatus.CANCELLED
    assert events(site.db, job_id)[-1] == ("cancelled", "anna", "Կրկնօրինակ է")
    again = post(site, job_id, "cancel")
    assert again.status_code == 409 and texts.ACTION_FAILED in again.text


def test_staff_set_the_group_and_lesson_and_the_held_job_continues(site):
    job_id = add_job(site.db, name="rec.mp4", status=JobStatus.ON_HOLD, stage=Stage.CHAPTERS,
                     hold_reason="identification",
                     flags=["unknown_group", "missing_lesson_number",
                            "empty_transcript"])  # fmt: skip
    response = post(site, job_id, "identify", group="Java 3", lesson="7", recorded="2026-11-19",
                    note="Ֆայլի անունը սխալ էր")  # fmt: skip
    assert response.status_code == 303
    state = job(site.db, job_id)
    assert (state.group, state.lesson, state.date) == ("Java 3", 7, date(2026, 11, 19))
    assert state.status is JobStatus.QUEUED and state.stage is Stage.CHAPTERS
    assert state.flags == ["empty_transcript"]
    assert [e[:2] for e in events(site.db, job_id)[-2:]] == [
        ("identified", "anna"),
        ("released", "anna"),
    ]
    with site.db.transaction() as s:
        identification = s.scalar(select(Recording)).identification
        assert identification["staff"]["by"] == "anna" and identification["staff"]["lesson"] == 7


@pytest.mark.parametrize(("typed", "saved"), [("java-3", "Java 3"), ("  Go   1 ", "Go 1")])
def test_the_group_is_typed_and_matched_to_groups_toml(site, typed, saved):
    job_id = add_job(site.db, status=JobStatus.ON_HOLD, stage=Stage.CHAPTERS,
                     hold_reason="identification")  # fmt: skip
    page = site.client.get(f"/recordings/{job_id}").text
    assert '<input name="group" list="known-groups" required' in page
    assert '<input name="lesson" type="text" inputmode="numeric"' in page
    assert post(site, job_id, "identify", group=typed, lesson="12").status_code == 303
    state = job(site.db, job_id)
    assert (state.group, state.lesson, state.status) == (saved, 12, JobStatus.QUEUED)
    assert list(load_groups(site.cfg.paths.groups_file).groups) == [
        "Python 12", "Java 3", *(["Go 1"] if saved == "Go 1" else []),
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("form", "status"),
    [
        ({"group": "  ", "lesson": "1"}, JobStatus.ON_HOLD),
        ({"group": "Java 3", "lesson": "է"}, JobStatus.ON_HOLD),  # the "1" key, Armenian layout
        ({"group": "Java 3", "lesson": "minus"}, JobStatus.ON_HOLD),
        ({"group": "Java 3", "lesson": "-2"}, JobStatus.ON_HOLD),
        ({"group": "Java 3", "recorded": "19.11.2026"}, JobStatus.ON_HOLD),
    ],
)
def test_wrong_identification_values_change_nothing(site, form, status):
    job_id = add_job(site.db, status=JobStatus.ON_HOLD, stage=Stage.CHAPTERS,
                     hold_reason="identification")  # fmt: skip
    response = post(site, job_id, "identify", **form)
    assert response.status_code == 409 and texts.ACTION_FAILED in response.text
    state = job(site.db, job_id)
    assert state.status is status and state.group is None


def test_a_running_job_cannot_be_identified(site):
    job_id = add_job(site.db, status=JobStatus.RUNNING, stage=Stage.CLEAN)
    assert post(site, job_id, "identify", group="Java 3").status_code == 409


def test_staff_rerun_from_a_chosen_stage(site):
    job_id = add_job(site.db, group="Python 12", lesson=2, status=JobStatus.DONE, stage=Stage.POST)
    assert post(site, job_id, "rerun", stage="clean", note="Վատ կտրում").status_code == 303
    state = job(site.db, job_id)
    assert state.status is JobStatus.QUEUED and state.stage is Stage.CLEAN
    assert events(site.db, job_id)[-1] == ("rerun", "anna", "Վատ կտրում")
    early = add_job(site.db, status=JobStatus.FAILED, stage=Stage.TRANSCRIBE)
    assert post(site, early, "rerun", stage="upload").status_code == 409  # would skip stages
    assert post(site, early, "rerun", stage="nonsense").status_code == 409


def test_staff_retry_now(site):
    job_id = add_job(site.db, stage=Stage.UPLOAD, last_error="quota",
                     next_attempt_at=utcnow() + timedelta(hours=5))  # fmt: skip
    assert "Փորձել հիմա" in site.client.get(f"/recordings/{job_id}").text
    assert post(site, job_id, "retry").status_code == 303
    assert job(site.db, job_id).next_attempt_at is None
    assert events(site.db, job_id)[-1][:2] == ("retry_now", "anna")
    assert post(site, job_id, "retry").status_code == 409  # not waiting any more


def test_actions_need_the_csrf_token_and_a_login(site):
    job_id = add_job(site.db, status=JobStatus.FAILED, stage=Stage.TRANSCRIBE)
    assert site.client.post(f"/recordings/{job_id}/cancel").status_code == 403
    assert job(site.db, job_id).status is JobStatus.FAILED
    site.client.cookies.clear()
    response = site.client.get(f"/recordings/{job_id}")
    assert response.headers["location"] == f"/login?next=%2Frecordings%2F{job_id}"
    assert site.client.get(f"/recordings/{job_id}/thumbnail").status_code == 303


def test_an_unknown_recording_is_not_found(site):
    page = site.client.get("/recordings/9999")
    assert page.status_code == 404 and texts.NOT_FOUND in page.text
    token = TOKEN.search(site.client.get("/").text)[1]  # the signed-in session's token
    missing = site.client.post("/recordings/9999/cancel", data={"csrf_token": token})
    assert missing.status_code == 404


def test_the_identification_flags_match_the_identifier():
    assert set(FLAGS) == IDENTIFICATION_FLAGS
