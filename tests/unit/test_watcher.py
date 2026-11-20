"""Folder watcher (T-202) with temporary folders and a fake clock."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from reel.cli import main
from reel.config import load_config
from reel.db import Job, Recording, SeenFile, SeenFileState, init_database, open_database
from reel.ingest.watcher import FolderWatcher

T0 = datetime(2026, 11, 19, 16, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def env(project, write_config):
    write_config('[ingest]\nwatch_folders = ["inbox", "offline"]\nstable_seconds = 5\n')
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    clock = Clock()
    yield project / "inbox", db, clock, FolderWatcher(db, lambda: cfg, clock=clock)
    db.dispose()


def write(path: Path, size: int = 1000, *, bump: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if bump:  # make sure the modification time moves on, as a real copy would
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + bump * 1_000_000_000))
    return path


def jobs(db) -> int:
    with db.transaction() as s:
        return s.scalar(select(func.count()).select_from(Job))


def states(db) -> dict[str, SeenFileState]:
    with db.transaction() as s:
        return {Path(row.path).name: row.state for row in s.scalars(select(SeenFile))}


def test_a_stable_file_is_queued_once(env):
    inbox, db, clock, watcher = env
    lesson = write(inbox / "Python 12" / "Lecture 14.mp4")
    assert watcher.scan().waiting == 1
    clock.advance(4)
    assert watcher.scan().queued == []  # stable for 4 s only
    clock.advance(1)
    (job_id,) = watcher.scan().queued
    clock.advance(60)
    assert watcher.scan().queued == []
    assert jobs(db) == 1
    with db.transaction() as s:
        recording = s.scalars(select(Recording)).one()
        assert recording.source_path == str(lesson)
        assert recording.size_bytes == 1000
        assert recording.detected_at == T0  # first sighting, for M-2
        assert recording.job.id == job_id
        row = s.scalars(select(SeenFile)).one()
        assert (row.state, row.recording_id) == (SeenFileState.QUEUED, recording.id)


def test_a_growing_file_is_not_queued(env):
    inbox, db, clock, watcher = env
    lesson = inbox / "lesson.mp4"
    for size in (100, 200, 300):
        write(lesson, size, bump=size)
        watcher.scan()
        clock.advance(4)
    assert jobs(db) == 0
    clock.advance(1)  # unchanged for 5 s now
    assert len(watcher.scan().queued) == 1


def test_a_restart_does_not_queue_the_file_again(env):
    inbox, db, clock, watcher = env
    write(inbox / "lesson.mp4")
    watcher.scan()
    clock.advance(10)
    watcher.scan()
    restarted = FolderWatcher(db, watcher._config, clock=clock)
    clock.advance(10)
    assert restarted.scan().queued == []
    assert jobs(db) == 1


def test_only_supported_and_visible_files_are_used(env):
    inbox, db, clock, watcher = env
    for name in (
        "notes.txt",
        "~lesson.mp4",
        ".hidden.mp4",
        "copying.mp4.part",
        "download.mkv.crdownload",
        ".sync/inside.mp4",
    ):
        write(inbox / name)
    write(inbox / "Group A" / "Դաս 3.MP4")  # upper-case extension, Armenian name, subfolder
    write(inbox / "b.mkv")
    assert watcher.scan().files == 2
    # Paths are stored case-insensitively on Windows (one row per file, however it is spelt).
    assert set(states(db)) == {os.path.normcase("Դաս 3.MP4"), "b.mkv"}


def test_a_file_deleted_before_it_is_stable_is_ignored(env):
    inbox, db, clock, watcher = env
    lesson = write(inbox / "lesson.mp4")
    watcher.scan()
    lesson.unlink()
    clock.advance(10)
    assert watcher.scan().gone == 1
    assert states(db) == {"lesson.mp4": SeenFileState.GONE}
    assert jobs(db) == 0
    write(lesson)  # copied again later: a new chance
    watcher.scan()
    clock.advance(5)
    assert len(watcher.scan().queued) == 1


def test_a_replaced_file_becomes_a_new_job(env):
    inbox, db, clock, watcher = env
    lesson = write(inbox / "lesson.mp4")
    watcher.scan()
    clock.advance(5)
    watcher.scan()
    write(lesson, 2000, bump=10)  # a corrected version under the same name
    watcher.scan()
    clock.advance(5)
    assert len(watcher.scan().queued) == 1
    assert jobs(db) == 2


def test_empty_files_wait(env):
    inbox, db, clock, watcher = env
    write(inbox / "placeholder.mp4", 0)  # e.g. a sync client's placeholder
    watcher.scan()
    clock.advance(60)
    assert watcher.scan().queued == []


def test_a_missing_watch_folder_keeps_its_files(env, project, write_config):
    inbox, db, clock, watcher = env
    offline = project / "offline"
    write(offline / "lesson.mp4")
    watcher.scan()
    offline_files = list(offline.iterdir())
    for path in offline_files:
        path.unlink()
    offline.rmdir()  # the drive was unplugged
    clock.advance(10)
    result = watcher.scan()
    assert result.missing_folders == [offline] and result.gone == 0
    assert states(db) == {"lesson.mp4": SeenFileState.WAITING}


def test_reel_watch_once(env, project, capsys):
    inbox, db, clock, watcher = env
    write(inbox / "lesson.mp4")
    args = ["--config", str(project / "config" / "reel.toml"), "watch", "--once"]
    assert main(args) == 0
    assert (
        "1 file(s): 1 waiting until fully written, 0 queued (jobs: none)" in capsys.readouterr().out
    )
