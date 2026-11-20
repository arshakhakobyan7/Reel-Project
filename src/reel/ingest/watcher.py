"""Folder watcher (T-202; FR-1, FR-2, D-024): polls the watch folders and turns every new,
fully written recording into exactly one queued import job.

A file is "fully written" when its size and modification time have not changed for
``ingest.stable_seconds``. All state is in ``seen_files``, so a restart never queues a file
again. A queued file that later changes (replaced by a new version) is treated as a new
file; the import stage's content hash still prevents processing the same content twice.
"""

import fnmatch
import logging
import os
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from reel.config.loader import LoadedConfig
from reel.db import Database, Recording, SeenFile, SeenFileState, utcnow
from reel.queue.jobs import create_job

log = logging.getLogger("reel.watcher")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
WAITING, QUEUED, GONE = SeenFileState.WAITING, SeenFileState.QUEUED, SeenFileState.GONE


@dataclass
class ScanResult:
    files: int = 0  # matching files found
    waiting: int = 0  # not fully written yet
    queued: list[int] = field(default_factory=list)  # ids of the jobs created in this scan
    gone: int = 0  # deleted before they were fully written
    missing_folders: list[Path] = field(default_factory=list)


def path_key(path: Path | str) -> str:
    """How a path is stored: absolute, and case-insensitive on Windows."""
    return os.path.normcase(os.path.abspath(path))


def _ignored(name: str, patterns: Iterable[str]) -> bool:
    lower = name.lower()
    return any(fnmatch.fnmatchcase(lower, pattern.lower()) for pattern in patterns)


def find_files(
    folder: Path, extensions: Iterable[str], patterns: Iterable[str]
) -> dict[str, tuple[Path, int, datetime]]:
    """Matching files under ``folder`` (recursive): key → (path, size, modification time).
    Ignored names also hide whole folders (e.g. ``.sync``)."""
    wanted = {extension.lower() for extension in extensions}
    patterns = tuple(patterns)
    found = {}
    for root, folders, names in os.walk(folder):
        folders[:] = [name for name in folders if not _ignored(name, patterns)]
        for name in names:
            if _ignored(name, patterns) or os.path.splitext(name)[1].lower() not in wanted:
                continue
            path = Path(root) / name
            try:
                info = path.stat()
            except OSError:  # deleted meanwhile
                continue
            modified = EPOCH + timedelta(microseconds=info.st_mtime_ns // 1000)
            found[path_key(path)] = (path, info.st_size, modified)
    return found


class FolderWatcher:
    def __init__(
        self,
        db: Database,
        config: Callable[[], LoadedConfig],
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.db = db
        self._config = config
        self._clock = clock
        self._missing: set[Path] = set()

    def scan(self) -> ScanResult:
        """One pass over all watch folders."""
        cfg = self._config()
        ingest = cfg.settings.ingest
        result = ScanResult()
        found: dict[str, tuple[Path, int, datetime]] = {}
        scanned: list[str] = []
        for folder in cfg.paths.watch_folders:
            if not folder.is_dir():  # e.g. an unplugged or not yet synced drive
                result.missing_folders.append(folder)
                continue
            scanned.append(path_key(folder))
            found.update(find_files(folder, ingest.extensions, ingest.ignore_patterns))
        result.files = len(found)
        stable_for = timedelta(seconds=ingest.stable_seconds)
        with self.db.transaction(immediate=True) as session:
            session.connection()  # take the write lock before reading
            now = self._clock()
            rows = {row.path: row for row in session.scalars(select(SeenFile))}
            for key, (path, size, modified) in found.items():
                row = rows.get(key)
                if row is None:
                    session.add(
                        SeenFile(
                            path=key,
                            size_bytes=size,
                            modified_at=modified,
                            first_seen_at=now,
                            stable_since=now,
                            last_checked_at=now,
                            state=WAITING,
                        )
                    )
                    result.waiting += 1
                    log.info("new file %s: waiting until it is fully written", path)
                    continue
                row.last_checked_at = now
                if (row.size_bytes, row.modified_at) != (size, modified) or row.state is GONE:
                    if row.state is not WAITING:  # replaced after queuing, or back again
                        log.info("file %s changed or came back: treated as a new file", path)
                        row.first_seen_at = now
                        row.recording_id = None
                        row.note = "changed or came back; treated as a new file"
                    row.size_bytes, row.modified_at = size, modified
                    row.stable_since, row.state = now, WAITING
                    result.waiting += 1
                elif row.state is WAITING:
                    if size > 0 and now - row.stable_since >= stable_for:
                        result.queued.append(self._enqueue(session, row, path, now))
                    else:
                        result.waiting += 1
            for key, row in rows.items():
                inside = any(key == f or key.startswith(f + os.sep) for f in scanned)
                if key not in found and row.state is WAITING and inside:
                    row.state, row.last_checked_at = GONE, now
                    row.note = "deleted before it was fully written"
                    result.gone += 1
        self._report_missing(result.missing_folders)
        return result

    def _enqueue(self, session: Session, row: SeenFile, path: Path, now: datetime) -> int:
        recording = Recording(
            source_path=str(path),
            size_bytes=row.size_bytes,
            file_modified_at=row.modified_at,
            detected_at=row.first_seen_at,  # M-2 counts from the first sighting
        )
        session.add(recording)
        job = create_job(session, recording, now=now)
        row.state, row.recording, row.note = QUEUED, recording, None
        log.info("queued %s as job %s", path, job.id, extra={"job_id": job.id})
        return job.id

    def _report_missing(self, folders: list[Path]) -> None:
        for folder in set(folders) - self._missing:
            log.warning(
                "watch folder %s does not exist (yet); its files are kept as they are", folder
            )
        self._missing = set(folders)

    def run(self, stop: threading.Event) -> None:
        """Scan every ``ingest.scan_interval_seconds`` until ``stop`` is set."""
        while not stop.is_set():
            try:
                self.scan()
            except Exception:
                log.exception("folder scan failed; trying again")
            stop.wait(self._config().settings.ingest.scan_interval_seconds)
