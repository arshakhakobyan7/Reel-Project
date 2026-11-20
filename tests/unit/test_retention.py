"""Data retention with a fake clock (T-902): only expired things go; dry-run only lists."""

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from test_web_dashboard import add_job

from reel.cli import main
from reel.config import load_config
from reel.db import (
    Job,
    JobEvent,
    JobStatus,
    PublicationTarget,
    Recording,
    Stage,
    Transcript,
    VideoMetadata,
    init_database,
    open_database,
)
from reel.db.enums import PublicationStatus, TargetKind, TranscriptKind
from reel.retention import ONLY_COPY, cleanup, daily

NOW = datetime(2026, 12, 31, 12, 0, tzinfo=UTC)


class Env:
    def __init__(self, project, write_config, extra=""):
        self.project, self.write_config = project, write_config
        self.configure(extra)
        init_database(self.cfg.paths.database_file)
        self.db = open_database(self.cfg.paths.database_file)

    def configure(self, extra=""):
        self.write_config(extra)
        self.cfg = load_config(self.project / "config" / "reel.toml")

    def job(self, status, days, *, files=True, transcripts=True, youtube="live", **fields):
        """``youtube``: "live" (really uploaded), "dry-run" or None (no upload row)."""
        job_id = add_job(self.db, status=status, stage=Stage.POST, **fields)
        moment = NOW - timedelta(days=days)
        with self.db.transaction() as s:
            if youtube is not None:
                live = youtube == "live"
                s.add(PublicationTarget(
                    job_id=job_id, target=TargetKind.YOUTUBE, status=PublicationStatus.PUBLISHED,
                    dry_run=not live, attempts=1, details={},
                    external_id=f"video{job_id}" if live else None,
                ))  # fmt: skip
            job = s.get(Job, job_id)
            job.updated_at = moment
            job.finished_at = moment if status is not JobStatus.FAILED else None
            if transcripts:
                for kind in TranscriptKind:
                    s.add(Transcript(job_id=job_id, kind=kind, language="hy", text="x",
                                     segments=[]))  # fmt: skip
            s.add(VideoMetadata(job_id=job_id, title=f"lesson {job_id}"))
        if files:
            folder = self.cfg.paths.data_dir / "jobs" / str(job_id)
            folder.mkdir(parents=True)
            (folder / "audio.wav").write_bytes(b"a" * 1000)
            (folder / "cleaned.mp4").write_bytes(b"v" * 5000)
        return job_id

    def upload(self, name, days, finished=False):
        folder = self.cfg.paths.data_dir / "uploads" / name
        folder.mkdir(parents=True)
        (folder / "01-part.mov").write_bytes(b"p" * 300)
        meta = {"created_at": (NOW - timedelta(days=days)).isoformat(), "finished": finished}
        (folder / "upload.json").write_text(json.dumps(meta), encoding="utf-8")
        return folder

    def transcripts(self, job_id):
        with self.db.transaction() as s:
            return s.scalar(select(func.count()).where(Transcript.job_id == job_id))

    def events(self, job_id):
        with self.db.transaction() as s:
            return s.scalars(select(JobEvent.message).where(
                JobEvent.job_id == job_id, JobEvent.kind == "cleanup")).all()  # fmt: skip


@pytest.fixture
def env(project, write_config):
    made = Env(project, write_config)
    yield made
    made.db.dispose()


def test_only_expired_things_are_removed_and_dry_run_only_lists(env):
    done_old = env.job(JobStatus.DONE, 40)
    done_new = env.job(JobStatus.DONE, 10)
    failed_ancient = env.job(JobStatus.FAILED, 400)
    running_old = env.job(JobStatus.RUNNING, 400)
    held_old = env.job(JobStatus.ON_HOLD, 400)
    stale = env.upload("a" * 24, 40)
    fresh = env.upload("b" * 24, 1)
    stranger = env.cfg.paths.data_dir / "uploads" / "not-reels"
    stranger.mkdir()
    jobs = env.cfg.paths.data_dir / "jobs"

    preview = cleanup(env.db, env.cfg, now=NOW, dry_run=True)
    expected = {
        f"job {done_old} working files", f"job {failed_ancient} working files",
        f"job {failed_ancient}: 2 transcript(s)", f"unfinished upload {stale.name}",
    }  # fmt: skip
    assert {r.detail for r in preview.removals} == expected
    upload_bytes = sum(item.stat().st_size for item in stale.iterdir())  # part + upload.json
    assert preview.bytes == 2 * 6000 + upload_bytes
    assert (jobs / str(done_old)).is_dir() and stale.is_dir()  # nothing deleted
    assert env.transcripts(failed_ancient) == 2 and env.events(done_old) == []

    report = cleanup(env.db, env.cfg, now=NOW)
    assert {r.detail for r in report.removals} == expected and not report.errors
    assert not (jobs / str(done_old)).exists() and not (jobs / str(failed_ancient)).exists()
    for kept in (done_new, running_old, held_old):
        assert (jobs / str(kept) / "cleaned.mp4").is_file()
    assert not stale.exists() and fresh.is_dir() and stranger.is_dir()
    assert env.transcripts(failed_ancient) == 0 and env.transcripts(done_old) == 2  # 365 days
    with env.db.transaction() as s:
        assert s.scalar(select(func.count()).select_from(VideoMetadata)) == 5  # titles stay
    assert env.events(failed_ancient) == ["retention: removed working_files, transcripts (0 MB)"]
    assert cleanup(env.db, env.cfg, now=NOW).removals == []  # nothing is left to do


def test_zero_days_keeps_everything(project, write_config):
    env = Env(project, write_config, "[retention]\nworking_files_days = 0\ntranscripts_days = 0\n")
    with env.db:
        env.job(JobStatus.DONE, 3000)
        env.upload("c" * 24, 3000)
        assert cleanup(env.db, env.cfg, now=NOW).removals == []


def test_source_recordings_are_deleted_only_when_enabled(project, write_config):
    env = Env(project, write_config)
    with env.db:
        inbox = env.cfg.paths.watch_folders[0]
        source = inbox / "Python 12" / "lesson.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"s" * 700)
        outside = project / "elsewhere.mp4"
        outside.write_bytes(b"o" * 10)
        parts_dir = env.cfg.paths.data_dir / "recordings" / ("u" * 24)
        parts_dir.mkdir(parents=True)
        parts = [parts_dir / "01-a.mov", parts_dir / "02-b.mov"]
        for part in parts:
            part.write_bytes(b"p" * 50)
        done = env.job(JobStatus.DONE, 100, files=False, transcripts=False)
        far = env.job(JobStatus.DONE, 100, files=False, transcripts=False)
        multi = env.job(JobStatus.DONE, 100, files=False, transcripts=False)
        failed = env.job(JobStatus.FAILED, 100, files=False, transcripts=False)
        recent = env.job(JobStatus.DONE, 10, files=False, transcripts=False)
        with env.db.transaction() as s:
            for job_id, path in ((done, source), (far, outside), (failed, source),
                                 (recent, source), (multi, parts[0])):  # fmt: skip
                s.get(Job, job_id).recording.source_path = str(path)
            s.get(Job, multi).recording.media_info = {"parts": [{"path": str(p)} for p in parts]}
        assert cleanup(env.db, env.cfg, now=NOW).removals == []  # off by default (D-021)
        env.configure("[retention]\ndelete_source_recordings = true\nsource_recordings_days = 90\n")
        report = cleanup(env.db, env.cfg, now=NOW)
        assert {r.detail for r in report.removals} == {
            f"job {done} source lesson.mp4", f"job {multi} source 01-a.mov",
            f"job {multi} source 02-b.mov",
        }  # fmt: skip
        assert not source.exists() and not any(p.exists() for p in parts)
        assert outside.is_file()  # not inside a watched folder: never deleted


def test_a_done_video_that_is_not_on_youtube_is_kept(project, write_config):
    """D-057: while publishing is dry-run, the cleaned video here is the only copy."""
    env = Env(project, write_config, "[retention]\ndelete_source_recordings = true\n")
    with env.db:
        live = env.job(JobStatus.DONE, 400)
        preview = env.job(JobStatus.DONE, 400, youtube="dry-run")
        never = env.job(JobStatus.DONE, 400, youtube=None)
        cancelled = env.job(JobStatus.CANCELLED, 400, youtube=None)  # staff said no: removed
        for job_id in (live, preview):
            source = env.cfg.paths.watch_folders[0] / f"g{job_id}" / "lesson.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"s" * 10)
            with env.db.transaction() as s:
                s.get(Job, job_id).recording.source_path = str(source)
        report = cleanup(env.db, env.cfg, now=NOW)
        removed = {r.detail for r in report.removals if r.kind != "transcripts"}
        assert removed == {f"job {live} working files", f"job {cancelled} working files",
                           f"job {live} source lesson.mp4"}  # fmt: skip
        assert sorted(report.kept) == sorted([
            f"job {preview} working files: {ONLY_COPY}",
            f"job {never} working files: {ONLY_COPY}",
            f"job {preview} source lesson.mp4: {ONLY_COPY}",
        ])  # fmt: skip
        jobs = env.cfg.paths.data_dir / "jobs"
        for job_id in (preview, never):
            assert (jobs / str(job_id) / "cleaned.mp4").is_file()
        assert (env.cfg.paths.watch_folders[0] / f"g{preview}" / "lesson.mp4").is_file()
        # only the old transcript rows went (365 days); the video, title and chapters stay
        assert env.events(preview) == ["retention: removed transcripts (0 MB)"]


def test_reel_cleanup_command(env, capsys):
    env.job(JobStatus.DONE, 800)  # the command uses the real clock: old enough for it too
    config = str(env.project / "config" / "reel.toml")
    assert main(["--config", config, "cleanup", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Would remove: job 1 working files" in out and "Would remove 2 item(s)" in out
    env.job(JobStatus.DONE, 800, youtube="dry-run")
    assert main(["--config", config, "cleanup", "--dry-run"]) == 0
    assert "Kept: job 2 working files: the video is not on YouTube" in capsys.readouterr().out
    assert main(["--config", config, "cleanup"]) == 0
    assert (
        "Removed 3 item(s)" in capsys.readouterr().out
    )  # job 1: files and transcripts; job 2: transcripts


def test_the_daily_run(env):
    env.job(JobStatus.DONE, 800)  # old enough also for the real clock used here
    stop = threading.Event()
    runs = daily(env.db, lambda: env.cfg, stop, first_delay=0.01)
    first = next(runs)
    assert len(first.removals) == 2
    stop.set()
    assert list(runs) == []


def test_the_recording_page_survives_a_removed_thumbnail(env):
    job_id = env.job(JobStatus.DONE, 400)
    with env.db.transaction() as s:
        meta = s.scalar(select(VideoMetadata).where(VideoMetadata.job_id == job_id))
        meta.thumbnail_path = str(env.cfg.paths.data_dir / "jobs" / str(job_id) / "thumb.jpg")
        assert s.get(Job, job_id).recording is not None
    cleanup(env.db, env.cfg, now=NOW)
    from zoneinfo import ZoneInfo

    from reel.web.detail import load_detail

    with env.db.transaction() as s:
        detail = load_detail(s, job_id, ZoneInfo("Asia/Yerevan"))
    assert detail.metadata["has_thumbnail"] is False
    with env.db.transaction() as s:
        assert s.scalar(select(func.count()).select_from(Recording)) == 1
