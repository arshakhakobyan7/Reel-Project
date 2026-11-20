"""Stage ``consent``: every outcome, holds, staff decisions and the audit rows (T-602)."""

import json
from datetime import date

import pytest
from sqlalchemy import select

from reel.config import load_config
from reel.consent import ConsentDecision, ConsentProvider, StudentConsent, StudentStatus
from reel.db import (
    ConsentCheck,
    Job,
    JobStatus,
    Recording,
    Stage,
    init_database,
    open_database,
)
from reel.db.enums import ConsentResult
from reel.queue import JobQueue, StageHandler, StageRegistry, Worker, create_job
from reel.stages.consent import ConsentStage

CONFIG = """
[workers]
max_attempts = 2
backoff_base_seconds = 0.01
backoff_factor = 1.0
idle_poll_seconds = 0.02
"""


class Upload(StageHandler):
    """Stands in for the YouTube upload: records that it ran."""

    name = Stage.UPLOAD

    def __init__(self) -> None:
        self.ran: list[int] = []

    def run(self, ctx):
        self.ran.append(ctx.job_id)


class Scripted(ConsentProvider):
    name = "scripted"

    def __init__(self, *results: ConsentResult) -> None:
        self.results = list(results)
        self.asked: list[tuple] = []

    def check(self, group, lesson_date):
        self.asked.append((group, lesson_date))
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        status = StudentStatus.GRANTED if result is ConsentResult.PASSED else StudentStatus.DENIED
        students = () if result is ConsentResult.UNREACHABLE else (
            StudentConsent("s-1", StudentStatus.GRANTED), StudentConsent("s-2", status)
        )  # fmt: skip
        return ConsentDecision(result, "core-7", students, f"scripted {result.value}")


@pytest.fixture
def setup(project, write_config):
    opened = []

    def setup(*, group="Python 12"):
        write_config(CONFIG)
        cfg = load_config(project / "config" / "reel.toml")
        init_database(cfg.paths.database_file)
        db = open_database(cfg.paths.database_file)
        opened.append(db)
        with db.transaction() as s:
            recording = Recording(source_path="lesson.mp4", size_bytes=1, group_name=group)
            s.add(recording)
            job_id = create_job(s, recording, stage=Stage.CONSENT).id
        return cfg, db, job_id

    yield setup
    for db in opened:
        db.dispose()


def work(cfg, db, provider_factory, upload):
    registry = StageRegistry([ConsentStage(provider_factory), upload])
    attention: list = []
    worker = Worker(db, registry, lambda: cfg, notifier=attention.append)
    worker.start(until_idle=True)
    assert worker.join(30)
    return attention


def state(db, job_id):
    with db.transaction() as s:
        job = s.get(Job, job_id)
        checks = s.scalars(select(ConsentCheck).where(ConsentCheck.job_id == job_id)).all()
        s.expunge_all()
    return job, checks


def test_everyone_consented_goes_on_to_upload(setup):
    cfg, db, job_id = setup()
    provider, upload = Scripted(ConsentResult.PASSED), Upload()
    assert work(cfg, db, lambda cfg: provider, upload) == []
    job, (check,) = state(db, job_id)
    assert upload.ran == [job_id] and job.stage == Stage.POST
    assert provider.asked == [("Python 12", None)]  # no Core Engine id → the group name
    assert (check.result, check.provider, check.group_ref) == (
        ConsentResult.PASSED,
        "scripted",
        "core-7",
    )
    assert check.students == [
        {"id": "s-1", "status": "granted"},
        {"id": "s-2", "status": "granted"},
    ]
    assert check.released_by is None and check.checked_at is not None


@pytest.mark.parametrize("result", [ConsentResult.OPTED_OUT, ConsentResult.UNKNOWN])
def test_a_student_without_consent_holds_the_job_until_staff_accept(setup, result):
    cfg, db, job_id = setup()
    upload = Upload()
    (attention,) = work(cfg, db, lambda cfg: Scripted(result), upload)
    job, (check,) = state(db, job_id)
    assert (job.status, job.hold_reason) == (JobStatus.ON_HOLD, f"consent_{result.value}")
    assert "release it to publish anyway, or cancel" in job.hold_message
    assert upload.ran == []  # nothing reaches upload while on hold
    assert check.result is result and check.released_by is None
    assert attention.job.id == job_id
    work(cfg, db, lambda cfg: Scripted(result), upload)  # the worker leaves it alone
    assert upload.ran == []
    # Staff checked the video and accept it (UJ-3): recorded on the check row.
    JobQueue(db).release(job_id, "anna", advance=True, note="the student is not in the video")
    work(cfg, db, lambda cfg: Scripted(result), upload)
    job, (check,) = state(db, job_id)
    assert upload.ran == [job_id] and job.stage == Stage.POST
    assert (check.released_by, check.release_note) == ("anna", "the student is not in the video")
    assert check.released_at is not None


def test_cancel_ends_the_job_without_publishing(setup):
    cfg, db, job_id = setup()
    upload = Upload()
    work(cfg, db, lambda cfg: Scripted(ConsentResult.OPTED_OUT), upload)
    JobQueue(db).cancel(job_id, "anna", note="opted-out student is visible")
    work(cfg, db, lambda cfg: Scripted(ConsentResult.PASSED), upload)
    job, (check,) = state(db, job_id)
    assert (job.status, upload.ran, check.released_by) == (JobStatus.CANCELLED, [], None)


def test_an_unreachable_source_is_retried_then_held_and_checked_again(setup):
    cfg, db, job_id = setup()
    upload = Upload()
    provider = Scripted(ConsentResult.UNREACHABLE)
    (attention,) = work(cfg, db, lambda cfg: provider, upload)
    job, checks = state(db, job_id)
    assert len(provider.asked) == 2  # first attempt failed and was retried (max_attempts = 2)
    assert (job.status, job.hold_reason) == (JobStatus.ON_HOLD, "consent_unreachable")
    assert [c.result for c in checks] == [ConsentResult.UNREACHABLE] * 2 and upload.ran == []
    # The source works again; staff ask for a new check (the stage runs again).
    JobQueue(db).release(job_id, "anna")
    work(cfg, db, lambda cfg: Scripted(ConsentResult.PASSED), upload)
    job, checks = state(db, job_id)
    assert upload.ran == [job_id]
    assert [c.result for c in checks][-1] is ConsentResult.PASSED
    assert all(c.released_by is None for c in checks)  # "check again" is not an acceptance


def test_the_core_engine_group_id_and_lesson_date_are_asked(setup, project, write_config):
    groups = (project / "config" / "groups.toml").read_text(encoding="utf-8")
    write_config(
        groups.replace('core_engine_group_id = ""', 'core_engine_group_id = "ce-42"', 1),
        "groups.toml",
    )
    cfg, db, job_id = setup()
    with db.transaction() as s:
        s.get(Job, job_id).recording.recording_date = date(2026, 11, 18)
    provider = Scripted(ConsentResult.PASSED)
    work(cfg, db, lambda cfg: provider, Upload())
    assert provider.asked[0][0] == "ce-42"
    assert str(provider.asked[0][1]) == "2026-11-18"


def test_the_file_provider_from_the_config_is_used_by_default(setup, project):
    (project / "config" / "consent.json").write_text(
        json.dumps(
            {
                "Python 12": {
                    "group_ref": "core-12",
                    "students": [{"id": "a", "status": "granted"}, {"id": "b", "status": "denied"}],
                }
            }
        ),
        encoding="utf-8",
    )
    cfg, db, job_id = setup()
    upload = Upload()
    registry = StageRegistry([ConsentStage(), upload])
    worker = Worker(db, registry, lambda: cfg, notifier=lambda a: None)
    worker.start(until_idle=True)
    assert worker.join(30)
    job, (check,) = state(db, job_id)
    assert (job.hold_reason, check.provider, check.group_ref) == (
        "consent_opted_out",
        "file",
        "core-12",
    )
    assert upload.ran == []
