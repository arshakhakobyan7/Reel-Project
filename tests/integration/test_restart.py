"""Kill Reel in the middle of a stage, start it again (T-901 acceptance).

The job must continue from the stage that was running: finished stages are not repeated and
the publication happens once. Real processes are started and killed (TerminateProcess on
Windows); the stages are small stand-ins that write what they did to files.
"""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from sqlalchemy import select

from reel.config import load_config
from reel.db import Job, JobEvent, JobStatus, Recording, Stage, init_database, open_database
from reel.queue import create_job
from reel.runtime import process_alive

CHILD = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from reel.config.loader import ConfigProvider
    from reel.db import Job, JobStatus, Stage, open_database
    from reel.logging_setup import configure_from_config
    from reel.queue import StageHandler, StageRegistry
    from reel.runtime import Parts, Runtime

    config, marks, job_id = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])

    class Mark(StageHandler):
        def __init__(self, stage):
            self.name = stage

        def run(self, ctx):
            with (marks / f"{self.name.value}.log").open("a") as log:
                log.write(f"{ctx.attempt}\\n")
            if self.name is Stage.CLEAN and (marks / "hang").exists():
                (marks / "hanging").touch()
                while True:  # never ends by itself: the process is killed here
                    time.sleep(0.05)

    provider = ConfigProvider(config)
    configure_from_config(provider.get(), role="restart-test", console=False)
    db = open_database(provider.get().paths.database_file)
    runtime = Runtime(db, provider.get, parts=Parts(web=False, watcher=False, notifier=False),
                      registry=StageRegistry([Mark(stage) for stage in Stage]))
    runtime.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with db.transaction() as s:
            if s.get(Job, job_id).status is JobStatus.DONE:
                break
        time.sleep(0.05)
    runtime.stop(timeout=10)
    db.dispose()
    """
)


def wait_for(condition, seconds=60.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def lines(marks: Path, stage: Stage) -> list[str]:
    path = marks / f"{stage.value}.log"
    return path.read_text().split() if path.exists() else []


def test_a_killed_reel_continues_the_job_from_its_stage(project, write_config, tmp_path):
    write_config(
        "[workers]\ncount = 1\nidle_poll_seconds = 0.05\nmax_attempts = 3\n"
        "backoff_base_seconds = 0.01\nbackoff_factor = 1.0\nlease_seconds = 300\n"
    )
    config = project / "config" / "reel.toml"
    cfg = load_config(config)
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    marks = tmp_path / "marks"
    marks.mkdir()
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    with db:
        with db.transaction() as s:
            recording = Recording(source_path="lesson.mp4", size_bytes=1)
            s.add(recording)
            job_id = create_job(s, recording).id
        command = [sys.executable, str(script), str(config), str(marks), str(job_id)]

        (marks / "hang").touch()
        first = subprocess.Popen(command)
        try:
            assert wait_for(lambda: (marks / "hanging").exists()), "the clean stage did not start"
        finally:
            with db.transaction() as s:
                owner = s.get(Job, job_id).lease_owner
            # No clean-up, no graceful stop: like a crash or a power cut. On Windows the
            # venv's python.exe starts the real interpreter as another process (owner pid).
            pid = int(owner.split("-")[-2]) if owner else 0
            if pid and process_alive(pid):
                os.kill(pid, signal.SIGTERM)  # TerminateProcess on Windows
            first.kill()
            first.wait(30)
        assert wait_for(lambda: not process_alive(pid), 15)
        with db.transaction() as s:
            job = s.get(Job, job_id)
            assert job.status is JobStatus.RUNNING and job.stage is Stage.CLEAN

        (marks / "hang").unlink()
        started = time.monotonic()
        second = subprocess.run(command, timeout=120, check=False)
        assert second.returncode == 0
        assert time.monotonic() - started < 60  # not after the 300 s lease

        with db.transaction() as s:
            job = s.get(Job, job_id)
            assert job.status is JobStatus.DONE, job.last_error
            kinds = s.scalars(select(JobEvent.kind).where(JobEvent.job_id == job_id)).all()
        assert "lease_expired" in kinds
        assert lines(marks, Stage.IMPORT) == ["1"] and lines(marks, Stage.TRANSCRIBE) == ["1"]
        assert lines(marks, Stage.CLEAN) == ["1", "2"]  # killed in attempt 1, done in attempt 2
        for stage in (Stage.CHAPTERS, Stage.CONSENT, Stage.UPLOAD, Stage.POST):
            assert lines(marks, stage) == ["1"]  # published once
