"""Metrics, compute "cost" and the time budget (T-805), on seeded data."""

import re
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from test_web_dashboard import add_job

from reel.config import load_config
from reel.db import (
    Job,
    JobEvent,
    JobStatus,
    StaffUser,
    Stage,
    StageRun,
    init_database,
    open_database,
)
from reel.db.enums import ActorKind, StageRunStatus
from reel.llm import LLMClient, LLMResult, MeteredLLM
from reel.metrics import BUDGET_EVENT, BUDGET_FLAG, check_budget, summarize
from reel.queue import StageHandler, StageRegistry, Worker
from reel.web import create_app
from reel.web.security import LoginThrottle, hash_password

NOW = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
ZONE = ZoneInfo("Asia/Yerevan")  # UTC+4
CUTOFF = time(23, 59)
TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')


def run(s, job_id, stage, minutes, *, start, status=StageRunStatus.SUCCEEDED, metrics=None):
    s.add(StageRun(job_id=job_id, stage=stage, attempt=1, status=status, started_at=start,
                   finished_at=start + timedelta(minutes=minutes),
                   metrics=metrics or {}))  # fmt: skip
    return start + timedelta(minutes=minutes)


def utc(day, hour):
    return datetime(2026, 11, day, hour, 0, tzinfo=UTC)


def usage(calls, prompt, output, seconds):
    return {"calls": calls, "prompt_tokens": prompt, "output_tokens": output, "seconds": seconds}


def seed(db):
    """Four jobs with known times (see the expected numbers in the tests)."""
    one = add_job(db, group="Python 12", lesson=1, status=JobStatus.DONE, stage=Stage.POST)
    two = add_job(db, group="Python 12", lesson=2, status=JobStatus.DONE, stage=Stage.POST)
    three = add_job(db, group="Java 3", lesson=1, status=JobStatus.FAILED, stage=Stage.TRANSCRIBE)
    old = add_job(db, group="Java 3", lesson=0, status=JobStatus.DONE, stage=Stage.POST)
    with db.transaction() as s:
        times = {
            one: (utc(19, 14), utc(19, 16)),
            two: (utc(19, 18), utc(19, 21)),
            three: (utc(19, 10), None),
            old: (NOW - timedelta(days=60), NOW - timedelta(days=60) + timedelta(hours=1)),
        }  # fmt: skip
        for job_id, (created, finished) in times.items():
            job = s.get(Job, job_id)
            job.created_at, job.finished_at = created, finished
        t = run(s, one, Stage.TRANSCRIBE, 30, start=times[one][0])
        t = run(s, one, Stage.CLEAN, 20, start=t, metrics={
            "seconds": {"render": 600},
            "llm": usage(2, 1000, 100, 60)})  # fmt: skip
        run(s, one, Stage.CHAPTERS, 10, start=t, metrics={
            "llm": usage(5, 5000, 500, 300)})  # fmt: skip
        t = run(s, two, Stage.TRANSCRIBE, 10, start=times[two][0], status=StageRunStatus.FAILED)
        run(s, two, Stage.TRANSCRIBE, 300, start=t)
        s.add(JobEvent(job_id=two, kind="released", actor_kind=ActorKind.STAFF, actor_name="anna",
                       message="released by anna"))  # fmt: skip
        t = times[three][0]
        for _ in range(3):
            t = run(s, three, Stage.TRANSCRIBE, 5, start=t, status=StageRunStatus.FAILED)
        run(s, old, Stage.TRANSCRIBE, 20, start=times[old][0])
    return SimpleNamespace(one=one, two=two, three=three, old=old)


def test_the_summary_on_seeded_data(db):
    ids = seed(db)
    with db.transaction() as s:
        m = summarize(s, days=30, zone=ZONE, cutoff=CUTOFF, budget_minutes=240, now=NOW)
    assert m.jobs == 3 and m.by_status["done"] == 2 and m.by_status["failed"] == 1
    assert m.failed_attempts_by_stage["transcribe"] == 4
    assert m.failed_jobs_by_stage == {**{s.value: 0 for s in Stage}, "transcribe": 1}
    assert m.done == 2 and m.median_minutes_to_links == 150
    assert (m.same_evening, m.same_evening_rate) == (1, 0.5)  # 20:00 yes; 01:00 next day no
    assert (m.without_staff, m.without_staff_rate) == (1, 0.5)
    assert m.over_budget == 1  # 310 min > 240
    assert m.median_processing_minutes == 60  # of 60, 310 and 15
    assert m.whisper_hours == round(355 / 60, 3)
    assert m.render_hours == round(600 / 3600, 3) and m.llm_hours == round(360 / 3600, 3)
    assert m.llm_tokens == 6600
    first = next(job for job in m.jobs_detail if job.job_id == ids.one)
    assert first.stage_seconds == {"transcribe": 1800, "clean": 1200, "chapters": 600}
    assert first.llm_calls == 7 and first.file_to_links_minutes == 120 and first.same_evening
    data = m.to_dict()
    assert data["jobs_detail"][0]["processing_minutes"] == 60
    with db.transaction() as s:
        everything = summarize(s, days=0, zone=ZONE, cutoff=CUTOFF, budget_minutes=240, now=NOW)
    assert everything.jobs == 4 and everything.done == 3


def test_an_empty_period(db):
    with db.transaction() as s:
        m = summarize(s, days=7, zone=ZONE, cutoff=CUTOFF, budget_minutes=240, now=NOW)
    assert m.jobs == 0 and m.median_minutes_to_links is None and m.same_evening_rate is None


def test_the_budget_is_flagged_once(db):
    job_id = add_job(db, stage=Stage.CLEAN)
    with db.transaction() as s:
        run(s, job_id, Stage.TRANSCRIBE, 200, start=NOW - timedelta(hours=5))
    with db.transaction() as s:
        assert check_budget(s, job_id, 240) is None
    with db.transaction() as s:
        run(s, job_id, Stage.CLEAN, 50, start=NOW - timedelta(hours=1))
    with db.transaction() as s:
        assert check_budget(s, job_id, 240) == pytest.approx(250)
    with db.transaction() as s:
        assert check_budget(s, job_id, 240) is None  # only once
        job = s.get(Job, job_id)
        assert BUDGET_FLAG in job.flags
        event = s.scalar(select(JobEvent).where(JobEvent.kind == BUDGET_EVENT))
        assert "250 min" in event.message and event.data["budget"] == 240


class Quick(StageHandler):
    name = Stage.CLEAN

    def run(self, ctx):
        return None


def test_the_worker_checks_the_budget_after_each_stage(project, write_config):
    write_config("[workers]\nidle_poll_seconds = 0.02\n[budget]\nmax_processing_minutes = 10\n")
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    with db:
        job_id = add_job(db, stage=Stage.CLEAN)
        with db.transaction() as s:
            run(s, job_id, Stage.TRANSCRIBE, 11, start=NOW - timedelta(hours=1))
        worker = Worker(db, StageRegistry([Quick()]), lambda: cfg, notifier=lambda a: None)
        worker.start(until_idle=True)
        assert worker.join(30)
        with db.transaction() as s:
            assert BUDGET_FLAG in s.get(Job, job_id).flags


class Answer(BaseModel):
    text: str


class Counted(LLMClient):
    name = "counted"

    def generate(self, prompt, schema, *, system="", check=None):
        return LLMResult(schema(text="ok"), '{"text":"ok"}', "m", 120, 30, 1.5)


def test_the_llm_meter_adds_up_calls_tokens_and_seconds():
    meter = MeteredLLM(Counted())
    for _ in range(3):
        assert meter.generate("q", Answer).value.text == "ok"
    assert meter.usage() == {"calls": 3, "prompt_tokens": 360, "output_tokens": 90, "seconds": 4.5}
    meter.close()  # the wrapped client has no close(): nothing happens


def test_the_metrics_page_and_json(project, write_config):
    write_config("")
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    with db:
        ids = seed(db)
        with db.transaction() as s:
            s.add(
                StaffUser(
                    username="anna", password_hash=hash_password("metrics-password"), role="admin"
                )
            )
        app = create_app(db, lambda: cfg, "metrics-secret-" + "m" * 30, throttle=LoginThrottle())
        with TestClient(app, follow_redirects=False) as client:
            assert client.get("/metrics").status_code == 303  # login first
            token = TOKEN.search(client.get("/login").text)[1]
            client.post("/login", data={"username": "anna", "password": "metrics-password",
                                        "csrf_token": token})  # fmt: skip
            page = client.get("/metrics?days=0").text
            assert "<h1>Չափումներ</h1>" in page and "Նույն երեկոյան" in page
            assert "Բյուջեից ավել" in page and f'href="/recordings/{ids.two}"' in page
            data = client.get("/metrics.json?days=0").json()
            assert data["jobs"] == 4 and data["llm_tokens"] == 6600
            detail = client.get(f"/recordings/{ids.one}").text
            assert "Ծախս և ժամանակ" in detail and "6600 token" in detail
            assert "Ֆայլից մինչև հղումներ</dt><dd>2:00:00 · նույն երեկոյան" in detail
