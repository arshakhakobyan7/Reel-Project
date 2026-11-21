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
    StaffUser,
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

    def add(self, path: Path, owner: int | None = None, publish: bool = True) -> int:
        """Queue a file the way the watcher does (or an upload of ``owner``)."""
        with self.db.transaction() as s:
            recording = Recording(source_path=str(path), size_bytes=path.stat().st_size,
                                  uploaded_by_id=owner, publish=publish)  # fmt: skip
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


# --- T-1015: a user's upload is compared only with the same account's recordings ----------


def person(env: Env, name: str, role: str = "user", can_publish: bool = False) -> int:
    with env.db.transaction() as s:
        user = StaffUser(username=name, password_hash="x", role=role, can_publish=can_publish)
        s.add(user)
        s.flush()
        return user.id


def copy_of(env: Env, path: Path, name: str) -> Path:
    copy = env.inbox / "copies" / f"{name}.mp4"
    copy.parent.mkdir(exist_ok=True)
    shutil.copyfile(path, copy)
    return copy


def set_status(env: Env, job_id: int, status: JobStatus) -> None:
    with env.db.transaction() as s:
        s.get(Job, job_id).status = status


def test_a_users_upload_is_not_a_duplicate_of_another_accounts_video(env):
    admin = person(env, "admin", role="admin")
    anna, aram = person(env, "anna"), person(env, "aram")
    first = lesson(env, "Python 12/Lecture 1.mp4")
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    published = env.add(first, owner=admin)
    env.run()
    annas = env.add(copy_of(env, first, "anna"), owner=anna, publish=False)
    arams = env.add(copy_of(env, first, "aram"), owner=aram, publish=False)
    env.run()
    for job_id in (annas, arams):
        job, recording = env.job(job_id)
        assert (job.stage, job.status) == (Stage.TRANSCRIBE, JobStatus.QUEUED)
        assert (recording.content_hash, recording.processed_hash) == (None, digest)
        assert recording.duplicate_of_id is None
    assert env.job(published)[1].content_hash == digest
    # what is published is still published once (FR-4), whatever the users uploaded
    again = env.add(copy_of(env, first, "admin"), owner=admin)
    env.run()
    job, recording = env.job(again)
    assert job.status is JobStatus.CANCELLED
    assert recording.duplicate_of_id == env.job(published)[1].id


def test_a_user_uploading_the_same_file_again(env):
    anna = person(env, "anna")
    first = lesson(env, "Python 12/Lecture 1.mp4")
    original = env.add(first, owner=anna, publish=False)
    env.run()

    def upload_again(name: str) -> tuple[Job, Recording]:
        job_id = env.add(copy_of(env, first, name), owner=anna, publish=False)
        env.run()
        return env.job(job_id)

    # the first one is still in progress: not processed twice
    job, recording = upload_again("while-working")
    assert job.status is JobStatus.CANCELLED
    assert f"duplicate of recording {env.job(original)[1].id} (job {original}" in job.last_error
    assert recording.duplicate_of_id == env.job(original)[1].id
    # ready, and its video is here to download: still not processed twice
    set_status(env, original, JobStatus.DONE)
    video = env.cfg.paths.data_dir / "jobs" / str(original) / "cleaned.mp4"
    video.write_bytes(b"v")
    assert upload_again("while-ready")[0].status is JobStatus.CANCELLED
    # its video was deleted after 30 days (T-1014): processed again
    video.unlink()
    job, recording = upload_again("after-deletion")
    assert (job.stage, job.status, recording.duplicate_of_id) == (
        Stage.TRANSCRIBE, JobStatus.QUEUED, None
    )  # fmt: skip
    # a cancelled or failed earlier one does not block either
    set_status(env, job.id, JobStatus.CANCELLED)
    later, _ = upload_again("after-cancel")
    assert (later.stage, later.status) == (Stage.TRANSCRIBE, JobStatus.QUEUED)
    set_status(env, later.id, JobStatus.FAILED)
    assert upload_again("after-failure")[0].status is JobStatus.QUEUED


def test_a_user_is_not_told_about_another_accounts_recording(env):
    admin = person(env, "admin", role="admin")
    vahe = person(env, "vahe", can_publish=True)  # publishes, so FR-4 applies to him
    first = lesson(env, "Python 12/Lecture 1.mp4")
    env.add(first, owner=admin)
    env.run()
    copy = env.add(copy_of(env, first, "vahe"), owner=vahe)
    env.run()
    job, recording = env.job(copy)
    assert job.status is JobStatus.CANCELLED and recording.duplicate_of_id is not None
    assert "duplicate of a recording of another account" in job.last_error
    assert "Lecture 1" not in job.last_error and "job 1" not in job.last_error
