"""Reel in one process, dead-process leases, and the job commands (T-901)."""

import os
import socket
import time
import urllib.request

import pytest
from sqlalchemy import select

from reel.cli import main
from reel.config import load_config
from reel.db import (
    Job,
    JobEvent,
    JobStatus,
    Recording,
    Stage,
    StageRun,
    init_database,
    open_database,
)
from reel.db.enums import StageRunStatus
from reel.queue import JobQueue, RetryPolicy, StageHandler, StageRegistry, create_job
from reel.runtime import Parts, Runtime, gone_here, process_alive

HOST = socket.gethostname()


def test_process_alive():
    assert process_alive(os.getpid())
    assert not process_alive(0) and not process_alive(4_000_000_000 // 4 * 4)


@pytest.mark.parametrize(
    ("owner", "dead", "expected"),
    [
        (f"{HOST}-123-1", True, True),
        (f"{HOST}-123-2", False, False),  # still running
        (f"{HOST}-{os.getpid()}-1", True, False),  # this process
        ("OTHER-PC-123-1", True, False),  # another computer: its lease runs out by itself
        ("not a worker name", True, False),
    ],
)
def test_which_workers_are_gone(owner, dead, expected):
    assert gone_here(owner, host=HOST, alive=lambda pid: not dead) is expected


def add(db, stage=Stage.IMPORT, **fields):
    with db.transaction() as s:
        recording = Recording(source_path=f"C:\\inbox\\{fields.pop('name', 'a.mp4')}",
                              size_bytes=1, group_name=fields.pop("group", None))  # fmt: skip
        s.add(recording)
        job = create_job(s, recording, stage=stage)
        for key, value in fields.items():
            setattr(job, key, value)
        s.flush()
        return job.id


def test_the_lease_of_a_gone_worker_ends_at_once(db):
    queue = JobQueue(db, policy=RetryPolicy(backoff_base_seconds=0.001), lease_seconds=3600)
    job_id = add(db, Stage.CLEAN)
    assert queue.claim(f"{HOST}-999999-1").job_id == job_id
    assert queue.claim("someone-else") is None  # the lease still holds
    assert queue.expire_leases_of(lambda owner: owner.startswith("OTHER")) == []
    assert queue.expire_leases_of(lambda owner: owner == f"{HOST}-999999-1") == [job_id]
    time.sleep(0.05)  # the usual (here tiny) backoff after a lost attempt
    again = queue.claim("new-worker")
    assert again.job_id == job_id and again.stage is Stage.CLEAN and again.attempt == 2
    with db.transaction() as s:
        kinds = s.scalars(select(JobEvent.kind).where(JobEvent.job_id == job_id)).all()
        assert "lease_expired" in kinds


class Patient(StageHandler):
    """Runs until it is asked to stop (a cooperative long stage)."""

    name = Stage.CLEAN

    def run(self, ctx):
        while not ctx.wait(0.05):
            pass
        ctx.check()


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_everything_starts_together_and_stops_gracefully(project, write_config):
    write_config("[workers]\ncount = 1\nidle_poll_seconds = 0.05\nstop_grace_seconds = 10\n"
                 "[ingest]\nscan_interval_seconds = 1\n")  # fmt: skip
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    with db:
        job_id = add(db, Stage.CLEAN)
        port = free_port()
        runtime = Runtime(
            db,
            lambda: cfg,
            parts=Parts(),
            registry=StageRegistry([Patient()]),
            host="127.0.0.1",
            port=port,
            secret="runtime-secret-" + "r" * 30,
        )
        runtime.start()
        assert wait_for(lambda: runtime.web_started, 15)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=5) as page:
            assert page.status == 200 and "Մուտք" in page.read().decode("utf-8")

        def running():
            with db.transaction() as s:
                return s.get(Job, job_id).status is JobStatus.RUNNING

        assert wait_for(running, 15)
        assert runtime.alive() and {t.name for t in runtime.threads} == {
            "cleanup",
            "notifier",
            "watcher",
            "web",
        }
        runtime.stop(timeout=20)
        assert not runtime.alive() and not any(t.is_alive() for t in runtime.threads)
        with db.transaction() as s:
            job = s.get(Job, job_id)
            assert job.status is JobStatus.QUEUED and job.stage is Stage.CLEAN  # given back
            run = s.scalar(select(StageRun).where(StageRun.job_id == job_id))
            assert run.status is StageRunStatus.INTERRUPTED


def wait_for(condition, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def cli(project, write_config):
    write_config("")
    config = str(project / "config" / "reel.toml")
    cfg = load_config(config)
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    yield config, db
    db.dispose()


def test_reel_status_lists_and_shows_jobs(cli, capsys):
    config, db = cli
    failed = add(db, Stage.TRANSCRIBE, status=JobStatus.FAILED, last_error="whisper crashed",
                 name="x.mp4", group="Python 12")  # fmt: skip
    add(db, Stage.POST, status=JobStatus.DONE, name="y.mp4")
    assert main(["--config", config, "status"]) == 0
    out = capsys.readouterr().out
    assert "failed 1" in out and "done 1" in out and "whisper crashed" in out
    assert main(["--config", config, "status", "--status", "done"]) == 0
    out = capsys.readouterr().out
    assert "y.mp4" in out and "x.mp4" not in out
    assert main(["--config", config, "status", str(failed)]) == 0
    out = capsys.readouterr().out
    assert f"Job #{failed}: Python 12 · x.mp4" in out and "Last error: whisper crashed" in out
    assert main(["--config", config, "status", "999"]) == 1


def test_reel_job_actions_are_audited(cli, capsys):
    config, db = cli
    held = add(db, Stage.CONSENT, status=JobStatus.ON_HOLD, hold_reason="consent_opted_out")
    assert main(["--config", config, "release", str(held), "--accept", "--by", "anna",
                 "--note", "checked"]) == 0  # fmt: skip
    assert "Queued at Uploaded" in capsys.readouterr().out
    done = add(db, Stage.POST, status=JobStatus.DONE)
    assert main(["--config", config, "rerun", str(done), "--from", "clean", "--by", "anna"]) == 0
    assert main(["--config", config, "cancel", str(done), "--by", "anna"]) == 0
    assert main(["--config", config, "cancel", str(done), "--by", "anna"]) == 1  # already
    assert "cannot cancel" in capsys.readouterr().err
    assert main(["--config", config, "retry", str(held), "--by", "anna"]) == 1  # not waiting
    with db.transaction() as s:
        events = s.execute(
            select(JobEvent.kind, JobEvent.actor_name).where(JobEvent.actor_name == "anna")
        ).all()
    assert [kind for kind, _ in events] == ["released", "rerun", "cancelled"]
