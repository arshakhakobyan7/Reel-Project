"""Import stage (T-204) with real FFmpeg, synthetic recordings and the worker runtime."""

import hashlib
import shutil
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from reel.config import load_config
from reel.db import (
    Job,
    JobStatus,
    Recording,
    Stage,
    init_database,
    open_database,
)
from reel.media.ffmpeg import probe
from reel.media.testmedia import make_lesson, parse_parts
from reel.queue import JobQueue, StageRegistry, Worker, create_job
from reel.stages.importing import ImportStage

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "ffmpeg" / "bin"
FFMPEG, FFPROBE = BIN / "ffmpeg.exe", BIN / "ffprobe.exe"
pytestmark = pytest.mark.skipif(not FFMPEG.is_file(), reason="run scripts/setup_ffmpeg.py first")

CONFIG = f"""
[paths]
ffmpeg = '{FFMPEG.as_posix()}'
ffprobe = '{FFPROBE.as_posix()}'

[ingest]
min_duration_minutes = 0.1

[workers]
count = 2
max_attempts = 2
backoff_base_seconds = 0.01
backoff_factor = 1.0
idle_poll_seconds = 0.02
"""


def import_only() -> StageRegistry:
    """Only the stage under test (the next one would need a speech model)."""
    registry = StageRegistry()
    registry.register(ImportStage())
    return registry


class Env:
    def __init__(self, project: Path) -> None:
        self.inbox = project / "inbox"
        self.cfg = load_config(project / "config" / "reel.toml")
        init_database(self.cfg.paths.database_file)
        self.db = open_database(self.cfg.paths.database_file)
        self.queue = JobQueue.from_settings(self.db, self.cfg.settings.workers)
        self.attention = []

    def add(self, path: Path) -> int:
        """Queue a file the way the watcher does."""
        with self.db.transaction() as s:
            recording = Recording(source_path=str(path), size_bytes=path.stat().st_size)
            s.add(recording)
            return create_job(s, recording).id

    def run(self) -> None:
        worker = Worker(self.db, import_only(), lambda: self.cfg, notifier=self.attention.append)
        worker.start(until_idle=True)
        assert worker.join(120), "the worker did not finish"

    def job(self, job_id: int) -> tuple[Job, Recording]:
        with self.db.transaction() as s:
            job = s.get(Job, job_id)
            recording = job.recording
            s.expunge_all()
            return job, recording


@pytest.fixture
def env(project, write_config):
    write_config(CONFIG)
    env = Env(project)
    yield env
    env.db.dispose()


def lesson(env: Env, relative: str, parts: str = "moving:4:tone,static:4:silence") -> Path:
    path = env.inbox / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    return make_lesson(FFMPEG, path, parse_parts(parts))


def test_a_valid_file_moves_to_transcribe_with_its_artifacts(env):
    path = lesson(env, "Python 12/Lecture 14 2026-11-19.mp4")
    job_id = env.add(path)
    env.run()
    job, recording = env.job(job_id)
    assert (job.stage, job.status, job.attempts, job.flags) == (
        Stage.TRANSCRIBE,
        JobStatus.QUEUED,
        0,
        [],
    )
    assert recording.content_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert recording.original_duration_seconds == pytest.approx(8, abs=0.2)
    assert recording.media_info["video_codec"] and recording.media_info["audio_codec"]
    assert (recording.group_name, recording.lesson_number, recording.recording_date) == (
        "Python 12",
        14,
        date(2026, 11, 19),
    )
    audio = env.cfg.paths.data_dir / "jobs" / str(job_id) / "audio.wav"
    info = probe(FFPROBE, audio)
    assert (info.sample_rate, info.channels) == (16000, 1)
    assert info.duration_seconds == pytest.approx(8, abs=0.2)
    assert env.attention == []


def test_the_same_content_under_another_name_is_not_processed_again(env):
    first = lesson(env, "Python 12/Lecture 1.mp4")
    original = env.add(first)
    env.run()
    copy = env.inbox / "Py12" / "copy of lecture.mp4"
    copy.parent.mkdir()
    shutil.copyfile(first, copy)
    duplicate = env.add(copy)
    env.run()
    job, recording = env.job(duplicate)
    assert job.status is JobStatus.CANCELLED and job.stage is Stage.IMPORT
    assert f"duplicate of recording {env.job(original)[1].id} (job {original}" in job.last_error
    assert (recording.duplicate_of_id, recording.content_hash) == (env.job(original)[1].id, None)
    assert not (env.cfg.paths.data_dir / "jobs" / str(duplicate) / "audio.wav").exists()
    assert env.job(original)[0].stage is Stage.TRANSCRIBE
    assert env.attention == []  # a duplicate is not a problem for staff


def test_copies_imported_at_the_same_time_give_one_job(env):
    first = lesson(env, "Python 12/Lecture 2.mp4")
    copies = [first]
    for name in ("a.mp4", "b.mp4"):
        copies.append(env.inbox / "Python 12" / name)
        shutil.copyfile(first, copies[-1])
    jobs = [env.add(path) for path in copies]
    env.run()  # two worker slots hash at the same time
    statuses = sorted(str(env.job(job_id)[0].status) for job_id in jobs)
    assert statuses == ["cancelled", "cancelled", "queued"]


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (b"this is not a video", "not a usable recording"),
        ("moving:3:tone", "too short"),  # minimum 6 s in this config
    ],
)
def test_an_invalid_file_fails_at_once_and_notifies_staff(env, content, error):
    path = env.inbox / "Python 12" / "Lecture 3.mp4"
    if isinstance(content, bytes):
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
    else:
        lesson(env, "Python 12/Lecture 3.mp4", content)
    job_id = env.add(path)
    env.run()
    job, _ = env.job(job_id)
    assert job.status is JobStatus.FAILED and job.attempts == 1  # not retried
    assert error in job.last_error
    (attention,) = env.attention
    assert (attention.kind, attention.job.id, attention.stage) == ("failed", job_id, Stage.IMPORT)
    assert error in attention.message


def test_a_missing_file_fails_after_the_retries(env):
    path = lesson(env, "Python 12/Lecture 4.mp4")
    job_id = env.add(path)
    path.unlink()
    env.run()
    job, _ = env.job(job_id)
    assert job.status is JobStatus.FAILED and job.attempts == 2
    assert "no longer exists" in job.last_error


def test_unknown_names_are_flagged_and_processing_continues(env):
    job_id = env.add(lesson(env, "Unknown/recording.mp4"))
    env.run()
    job, recording = env.job(job_id)
    assert job.stage is Stage.TRANSCRIBE
    assert job.flags == ["unknown_group", "missing_lesson_number"]
    assert recording.identification["folders"] == ["Unknown"]


def test_a_rerun_of_import_is_safe(env):
    job_id = env.add(lesson(env, "Python 12/Lecture 5.mp4"))
    env.run()
    env.queue.rerun(job_id, "staff", Stage.IMPORT)
    env.run()
    job, recording = env.job(job_id)
    assert (job.stage, job.status) == (Stage.TRANSCRIBE, JobStatus.QUEUED)
    assert recording.content_hash and recording.duplicate_of_id is None
    with env.db.transaction() as s:
        assert len(s.scalars(select(Recording)).all()) == 1


def add_parts(env: Env, paths: list[Path], **identity) -> int:
    """Queue a lesson in parts the way an upload does (T-807)."""
    with env.db.transaction() as s:
        recording = Recording(
            source_path=str(paths[0]),
            size_bytes=sum(p.stat().st_size for p in paths),
            identification={"set_by_staff": True},
            media_info={"parts": [{"path": str(p), "name": p.name} for p in paths]},
            **identity,
        )
        s.add(recording)
        return create_job(s, recording).id


def test_a_lesson_in_two_parts_becomes_one_recording_with_joined_audio(env):
    import wave

    first = lesson(env, "upload/01-IMG_0359.mp4", "moving:4:tone,static:3:silence")
    second = lesson(env, "upload/02-IMG_0360 lesson 9.mp4", "moving:5:tone")
    job_id = add_parts(env, [first, second], group_name="Python 12")
    env.run()
    job, recording = env.job(job_id)
    assert job.status is JobStatus.QUEUED and job.stage is Stage.TRANSCRIBE, job.last_error
    durations = [probe(FFPROBE, p).duration_seconds for p in (first, second)]
    assert recording.original_duration_seconds == pytest.approx(sum(durations))
    parts = recording.media_info["parts"]
    assert [p["path"] for p in parts] == [str(first), str(second)]
    assert [p["duration_seconds"] for p in parts] == pytest.approx(durations)
    assert parts[0]["name"] == "01-IMG_0359.mp4"  # kept from the upload
    digest = hashlib.sha256(first.read_bytes() + second.read_bytes()).hexdigest()
    assert recording.content_hash == digest
    # Staff set the group; the lesson number they left empty comes from the file names.
    assert recording.group_name == "Python 12" and recording.lesson_number is None
    audio = env.cfg.paths.data_dir / "jobs" / str(job_id) / "audio.wav"
    with wave.open(str(audio), "rb") as joined:
        frames = joined.getnframes()
    assert frames == sum(round(d * 16000) for d in durations)  # exact part boundaries
    assert not list(audio.parent.glob("audio-part*.wav"))


def test_staff_values_are_kept_and_only_empty_ones_filled(env):
    path = lesson(env, "Java 3/Lesson 12 2026-11-18.mp4")
    job_id = add_parts(env, [path], group_name="Python 12", lesson_number=None)
    env.run()
    _, recording = env.job(job_id)
    assert recording.group_name == "Python 12"  # not the folder's group
    assert recording.lesson_number == 12 and str(recording.recording_date) == "2026-11-18"
    assert recording.identification == {"set_by_staff": True}


def test_a_part_that_is_not_a_video_fails_the_lesson(env):
    first = lesson(env, "upload/01.mp4")
    second = env.inbox / "upload" / "02.mp4"
    second.write_bytes(b"not a video at all")
    job_id = add_parts(env, [first, second], group_name="Python 12")
    env.run()
    job, _ = env.job(job_id)
    assert job.status is JobStatus.FAILED and "part 02.mp4" in job.last_error
