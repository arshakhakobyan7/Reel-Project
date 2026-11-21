"""Recordings uploaded through the dashboard, in resumable chunks (T-807; D-035, D-051).

An upload holds the files of one lesson (one file, or several parts in recording order).
Each file is written to ``data/uploads/<id>/`` piece by piece; a file's size on disk is how
far it got, so a dropped connection or a restart resumes from there. When every file is
complete, the folder moves to ``data/recordings/<id>/`` in one rename, and one recording and
one job are created with the group, lesson and date the staff member chose. The normal
pipeline takes over from the import stage.

Nothing is loaded whole into memory: pieces go straight to the file.
"""

import json
import logging
import os
import re
import secrets
import shutil
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from reel.db import Job, Recording, utcnow

log = logging.getLogger("reel.ingest.upload")

UPLOADS = "uploads"
RECORDINGS = "recordings"
META = "upload.json"
MAX_FILES = 10
MAX_PIECE_BYTES = 64 * 1024 * 1024  # one request
MIN_FREE_BYTES = 2 * 1024**3  # keep this much disk free
_ID = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")
_UNSAFE = re.compile(r"[^\w.\-() ]+")


class UploadError(Exception):
    """A refused upload request. ``status`` is the HTTP status for the dashboard."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def safe_name(name: str) -> str:
    """The file's own name without folders or unusual characters (kept short)."""
    base = re.split(r"[\\/]", name)[-1].strip()
    cleaned = _UNSAFE.sub("_", base).strip(" .") or "recording"
    stem, dot, ext = cleaned.rpartition(".")
    return (stem[:80] + dot + ext[:10]) if dot else cleaned[:90]


@dataclass(frozen=True)
class Lesson:
    """What the staff member chose in the upload form."""

    group: str
    lesson: int | None
    recording_date: date | None
    note: str = ""


class UploadStore:
    def __init__(
        self,
        data_dir: Path,
        *,
        extensions: tuple[str, ...],
        max_file_bytes: int,
        min_free_bytes: int = MIN_FREE_BYTES,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.root = Path(data_dir) / UPLOADS
        self.finished_root = Path(data_dir) / RECORDINGS
        self.extensions = tuple(e.lower() for e in extensions)
        self.max_file_bytes = max_file_bytes
        self.min_free_bytes = min_free_bytes
        self.clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # --- storage ---------------------------------------------------------------------------

    def _dir(self, upload_id: str) -> Path:
        if not isinstance(upload_id, str) or not _ID.fullmatch(upload_id):
            raise UploadError("Վերբեռնումը չի գտնվել։", 404)
        return self.root / upload_id

    def _read(self, upload_id: str) -> dict[str, Any]:
        path = self._dir(upload_id) / META
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise UploadError("Վերբեռնումը չի գտնվել։", 404) from None

    def _write(self, upload_id: str, meta: dict[str, Any]) -> None:
        path = self._dir(upload_id) / META
        partial = path.with_name(META + ".part")
        partial.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(partial, path)

    def _lock(self, upload_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(upload_id, threading.Lock())

    # --- the steps -------------------------------------------------------------------------

    def create(self, by: str, lesson: Lesson, files: list[dict[str, Any]]) -> dict[str, Any]:
        """Start an upload of ``files`` (``name``, ``size``, optional ``modified`` in ms) in
        the order given, which is the order of the parts."""
        if not files or not isinstance(files, list):
            raise UploadError("Ընտրեք առնվազն մեկ ֆայլ։")
        if len(files) > MAX_FILES:
            raise UploadError(f"Մեկ դասի համար առավելագույնը {MAX_FILES} ֆայլ։")
        entries, total = [], 0
        for index, item in enumerate(files):
            name = str(item.get("name") or "") if isinstance(item, dict) else ""
            size = item.get("size") if isinstance(item, dict) else None
            if not name or type(size) is not int or size <= 0:
                raise UploadError("Ֆայլի անունը կամ չափը սխալ է։")
            ext = Path(name).suffix.lower()
            if ext not in self.extensions:
                allowed = ", ".join(self.extensions)
                raise UploadError(
                    f"«{name}» ֆայլի տեսակը չի ընդունվում (թույլատրված են՝ {allowed})։"
                )
            if size > self.max_file_bytes:
                limit = self.max_file_bytes / 1024**3
                raise UploadError(f"«{name}» ֆայլը մեծ է {limit:g} ԳԲ սահմանից։", 413)
            modified = item.get("modified")
            if not isinstance(modified, int | float) or isinstance(modified, bool):
                modified = None
            entries.append({
                "index": index, "name": name, "size": size, "modified_ms": modified,
                "stored": f"{index + 1:02d}-{safe_name(name)}",
            })  # fmt: skip
            total += size
        self.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.root).free
        if free - total < self.min_free_bytes:
            raise UploadError("Սկավառակի վրա բավարար տեղ չկա այս ֆայլերի համար։", 507)
        upload_id = secrets.token_urlsafe(18)
        folder = self._dir(upload_id)
        folder.mkdir()
        meta = {
            "id": upload_id, "created_by": by, "created_at": self.clock().isoformat(),
            "group": lesson.group, "lesson": lesson.lesson,
            "date": lesson.recording_date.isoformat() if lesson.recording_date else None,
            "note": lesson.note, "files": entries, "finished": False,
        }  # fmt: skip
        for entry in entries:
            (folder / entry["stored"]).touch()
        self._write(upload_id, meta)
        log.info("upload %s started by %s: %d file(s), %d bytes", upload_id, by,
                 len(entries), total)  # fmt: skip
        return self.status(upload_id)

    def owner(self, upload_id: str) -> str:
        """The username that started the upload (T-1013)."""
        return str(self._read(upload_id).get("created_by", ""))

    def status(self, upload_id: str) -> dict[str, Any]:
        meta = self._read(upload_id)
        folder = self._dir(upload_id)
        files = []
        for entry in meta["files"]:
            path = folder / entry["stored"]
            received = path.stat().st_size if path.is_file() else 0
            files.append({"index": entry["index"], "name": entry["name"], "size": entry["size"],
                          "received": received})  # fmt: skip
        return {"id": upload_id, "files": files, "finished": meta["finished"],
                "group": meta["group"]}  # fmt: skip

    @contextmanager
    def writer(self, upload_id: str, index: int, offset: int) -> Iterator[Callable[[bytes], None]]:
        """Append a piece of file ``index`` that starts at ``offset`` (it must be where the
        file ends now). Yields ``write(bytes)``; the bytes are on disk when the block ends,
        also when the connection drops in the middle."""
        meta = self._read(upload_id)
        if meta["finished"]:
            raise UploadError("Վերբեռնումն արդեն ավարտված է։", 409)
        files = meta["files"]
        if type(index) is not int or not 0 <= index < len(files):
            raise UploadError("Այդպիսի ֆայլ այս վերբեռնման մեջ չկա։", 404)
        entry = files[index]
        lock = self._lock(upload_id)
        if not lock.acquire(blocking=False):
            raise UploadError("Այս վերբեռնումն արդեն գրվում է մեկ այլ հարցմամբ։", 409)
        try:
            path = self._dir(upload_id) / entry["stored"]
            current = path.stat().st_size
            if offset != current:
                raise UploadError(f"offset {offset} != {current}", 409)
            written = 0

            with path.open("ab") as file:

                def write(data: bytes) -> None:
                    nonlocal written
                    written += len(data)
                    if written > MAX_PIECE_BYTES:
                        raise UploadError("Մասը չափազանց մեծ է։", 413)
                    if current + written > entry["size"]:
                        raise UploadError("Ֆայլն ավելի մեծ է, քան հայտարարված էր։", 400)
                    file.write(data)

                try:
                    yield write
                finally:
                    file.flush()
                    os.fsync(file.fileno())
        finally:
            lock.release()

    def cancel(self, upload_id: str) -> None:
        meta = self._read(upload_id)
        if meta["finished"]:
            raise UploadError("Վերբեռնումն արդեն ավարտված է։", 409)
        shutil.rmtree(self._dir(upload_id))
        log.info("upload %s cancelled", upload_id)

    def finish(
        self, upload_id: str, session_factory: Callable[[], Any], by: str, *,
        owner_id: int | None = None, publish: bool = True,
    ) -> int:  # fmt: skip
        """Check every file is complete, move them into ``data/recordings/<id>/`` and create
        the recording and its job. Returns the job id. ``owner_id``: the account that
        uploaded it; ``publish=False``: the recording is only processed (T-1013)."""
        lock = self._lock(upload_id)
        if not lock.acquire(blocking=False):
            raise UploadError("Այս վերբեռնումն արդեն գրվում է մեկ այլ հարցմամբ։", 409)
        try:
            meta = self._read(upload_id)
            if meta["finished"]:
                raise UploadError("Վերբեռնումն արդեն ավարտված է։", 409)
            status = self.status(upload_id)
            incomplete = [f["name"] for f in status["files"] if f["received"] != f["size"]]
            if incomplete:
                raise UploadError("Ֆայլերը դեռ ամբողջությամբ չեն վերբեռնվել՝ "
                                  + ", ".join(incomplete), 409)  # fmt: skip
            source = self._dir(upload_id)
            for entry in meta["files"]:
                if entry["modified_ms"]:
                    seconds = entry["modified_ms"] / 1000
                    os.utime(source / entry["stored"], (seconds, seconds))
            meta["finished"] = True
            self._write(upload_id, meta)
            self.finished_root.mkdir(parents=True, exist_ok=True)
            target = self.finished_root / upload_id
            os.replace(source, target)
            try:
                with session_factory() as session:
                    job_id = create_lesson(
                        session, meta, target, by, owner_id=owner_id, publish=publish
                    )
            except Exception:
                os.replace(target, source)  # the upload stays, and can be finished again
                meta["finished"] = False
                self._write(upload_id, meta)
                raise
        finally:
            lock.release()
        log.info("upload %s finished by %s: job %s", upload_id, by, job_id)
        return job_id


def create_lesson(
    session: Session, meta: dict[str, Any], folder: Path, by: str, *,
    owner_id: int | None = None, publish: bool = True,
) -> int:  # fmt: skip
    """One recording and one job for the uploaded files (D-035): one file is a recording like
    a watched one; several files are its parts, in order (``media_info["parts"]``)."""
    from reel.queue import create_job

    files = meta["files"]
    paths = [folder / entry["stored"] for entry in files]
    first = files[0]
    modified = (
        datetime.fromtimestamp(first["modified_ms"] / 1000, UTC) if first["modified_ms"] else None
    )
    recording = Recording(
        source_path=str(paths[0].resolve()),
        size_bytes=sum(entry["size"] for entry in files),
        file_modified_at=modified,
        uploaded_by_id=owner_id,
        publish=publish,
        group_name=meta["group"],
        lesson_number=meta["lesson"],
        recording_date=date.fromisoformat(meta["date"]) if meta["date"] else None,
        identification={
            "set_by_staff": True,
            "staff": {
                "by": by,
                "at": utcnow().isoformat(),
                "group": meta["group"],
                "lesson": meta["lesson"],
                "date": meta["date"],
                "source": "upload",
            },
            "upload": {
                "id": meta["id"],
                "files": [entry["name"] for entry in files],
                "note": meta["note"],
            },
        },  # fmt: skip
    )
    if len(paths) > 1:
        recording.media_info = {
            "parts": [
                {"path": str(path.resolve()), "name": entry["name"], "size": entry["size"]}
                for path, entry in zip(paths, files, strict=True)
            ]
        }
    session.add(recording)
    job: Job = create_job(session, recording)
    session.flush()
    return job.id
