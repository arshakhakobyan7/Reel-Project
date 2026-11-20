"""The recordings list of the dashboard (T-803)."""

import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from reel.config import load_config
from reel.db import (
    CutList,
    Job,
    JobStatus,
    Recording,
    StaffUser,
    Stage,
    init_database,
    open_database,
    utcnow,
)
from reel.queue import create_job
from reel.web import create_app
from reel.web.security import LoginThrottle, hash_password
from reel.web.views import PAGE_SIZE, clock_text, pills

SECRET = "dashboard-test-secret-" + "z" * 30
PASSWORD = "dashboard-password-1"
TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')


def add_job(db, *, name="lesson.mp4", group=None, lesson=None, status=JobStatus.QUEUED,
            stage=Stage.IMPORT, **fields):  # fmt: skip
    with db.transaction() as s:
        recording = Recording(source_path=f"C:\\inbox\\{name}", size_bytes=1, group_name=group,
                              lesson_number=lesson,
                              original_duration_seconds=fields.pop("original", None))  # fmt: skip
        s.add(recording)
        job = create_job(s, recording, stage=stage)
        final = fields.pop("final", None)
        if final is not None:
            s.flush()
            s.add(CutList(job_id=job.id, version=1, original_duration_seconds=final + 60,
                          final_duration_seconds=final,
                          segments=[{"start": 0.0, "end": final, "action": "keep",
                                     "reason": "lesson"}]))  # fmt: skip
        job.status = status
        for key, value in fields.items():
            setattr(job, key, value)
        s.flush()
        return job.id


@pytest.fixture
def site(project, write_config):
    write_config("")
    write_config('[groups."Python 12"]\n[groups."Java 3"]\n', "groups.toml")
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    with db.transaction() as s:
        s.add(StaffUser(username="anna", password_hash=hash_password(PASSWORD)))
    app = create_app(db, lambda: cfg, SECRET, throttle=LoginThrottle())
    with TestClient(app, follow_redirects=False) as client:
        token = TOKEN.search(client.get("/login").text)[1]
        data = {"username": "anna", "password": PASSWORD, "csrf_token": token}
        assert client.post("/login", data=data).status_code == 303
        yield SimpleNamespace(client=client, db=db)
    db.dispose()


def rows(html):
    return re.findall(r'<tr class="job status-(\w+)">', html)


def test_every_status_is_listed_with_pills_durations_problems_and_flags(site):
    db = site.db
    later = utcnow() + timedelta(hours=1)
    ids = {
        "queued": add_job(db, name="new.mp4"),
        "running": add_job(db, group="Python 12", lesson=14, status=JobStatus.RUNNING,
                           stage=Stage.CLEAN, original=9672.0),
        "retry": add_job(db, group="Python 12", lesson=15, stage=Stage.UPLOAD,
                         last_error="YouTube: quota", next_attempt_at=later),
        "on_hold": add_job(db, group="Java 3", lesson=2, status=JobStatus.ON_HOLD,
                           stage=Stage.CONSENT, hold_reason="consent_opted_out",
                           hold_message="Ուսանողը համաձայն չէ"),
        "failed": add_job(db, name="<script>bad.mp4", status=JobStatus.FAILED,
                          stage=Stage.TRANSCRIBE, last_error="the recording no longer exists",
                          flags=["unknown_group", "new_flag_code"]),
        "done": add_job(db, group="Python 12", lesson=13, status=JobStatus.DONE,
                        stage=Stage.POST, original=9672.0, final=6723.0,
                        flags=["youtube_no_playlist"]),
        "cancelled": add_job(db, group="Java 3", lesson=1, status=JobStatus.CANCELLED,
                             stage=Stage.CHAPTERS),
    }  # fmt: skip
    page = site.client.get("/")
    assert page.status_code == 200 and '<meta http-equiv="refresh" content="30">' in page.text
    html = page.text
    assert rows(html) == [
        "cancelled",
        "done",
        "failed",
        "on_hold",
        "queued",
        "running",
        "queued",
    ]  # newest first; the retry job is queued
    assert "Python 12 · Դաս 13" in html and "Java 3 · Դաս 2" in html
    assert "&lt;script&gt;bad.mp4" in html and "<script>" not in html
    assert "2:41:12 → 1:52:03" in html  # original → final
    assert "Ուսանողը համաձայն չէ" in html and "the recording no longer exists" in html
    assert "YouTube: quota" in html and "Կրկին փորձ է սպասվում" in html
    assert "Խումբն անհայտ է" in html and "new_flag_code" in html
    assert "YouTube playlist չկա" in html
    for label in ("Հերթում", "Ընթացքում", "Սպասում է որոշման", "Ձախողված", "Պատրաստ", "Չեղարկված"):
        assert label in html
    assert f'href="/recordings/{ids["done"]}"' in html
    done = html.split(f'href="/recordings/{ids["done"]}"')[1].split("</tr>")[0]
    assert done.count("pill-done") == 7
    held = html.split(f'href="/recordings/{ids["on_hold"]}"')[1].split("</tr>")[0]
    assert held.count("pill-done") == 4 and "pill-held" in held and held.count("pill-pending") == 2


@pytest.mark.parametrize(
    ("stage", "status", "retry", "expected"),
    [
        (Stage.IMPORT, JobStatus.QUEUED, False, ["queued"] + ["pending"] * 6),
        (Stage.CLEAN, JobStatus.RUNNING, False, ["done"] * 2 + ["running"] + ["pending"] * 4),
        (Stage.UPLOAD, JobStatus.QUEUED, True, ["done"] * 5 + ["retry", "pending"]),
        (Stage.POST, JobStatus.FAILED, False, ["done"] * 6 + ["failed"]),
        (
            Stage.CHAPTERS,
            JobStatus.CANCELLED,
            False,
            ["done"] * 3 + ["cancelled"] + ["pending"] * 3,
        ),
        (Stage.POST, JobStatus.DONE, False, ["done"] * 7),
    ],
)
def test_pill_states(stage, status, retry, expected):
    assert [p.state for p in pills(stage, status, waiting_retry=retry)] == expected


def test_filters_by_status_and_group(site):
    add_job(site.db, group="Python 12", lesson=1, status=JobStatus.FAILED)
    add_job(site.db, group="Java 3", lesson=1, status=JobStatus.FAILED)
    add_job(site.db, group="Java 3", lesson=2, status=JobStatus.DONE)
    add_job(site.db, name="unknown.mp4", status=JobStatus.FAILED)
    assert rows(site.client.get("/?status=failed").text) == ["failed"] * 3
    assert len(rows(site.client.get("/?group=Java+3").text)) == 2
    html = site.client.get("/?status=failed&group=Java+3").text
    assert rows(html) == ["failed"] and "Java 3 · Դաս 1" in html
    assert "unknown.mp4" in site.client.get("/?group=-").text
    assert len(rows(site.client.get("/?status=nonsense").text)) == 4  # ignored
    empty = site.client.get("/?status=running").text
    assert "Այս ընտրությամբ ձայնագրություն չկա" in empty
    assert '<option value="Python 12"' in empty and '<option value="Java 3"' in empty


def test_long_lists_have_pages(site):
    for number in range(PAGE_SIZE + 3):
        add_job(site.db, group="Python 12", lesson=number)
    first = site.client.get("/").text
    assert len(rows(first)) == PAGE_SIZE and "Էջ 1 / 2" in first and "page=2" in first
    second = site.client.get("/?page=2").text
    assert len(rows(second)) == 3 and "Python 12 · Դաս 0" in second
    assert len(rows(site.client.get("/?page=99").text)) == 3  # the last page


def test_the_list_needs_login(site):
    site.client.cookies.clear()
    assert site.client.get("/?status=failed").status_code == 303


def test_clock_text():
    assert clock_text(None) == "" and clock_text(65.9) == "1:05"
    assert clock_text(9672) == "2:41:12" and clock_text(float("nan")) == ""


def test_job_rows_in_the_database_are_untouched_by_viewing(site):
    job_id = add_job(site.db, status=JobStatus.FAILED, last_error="x")
    with site.db.transaction() as s:
        before = s.get(Job, job_id).version
    site.client.get("/")
    with site.db.transaction() as s:
        assert s.get(Job, job_id).version == before
