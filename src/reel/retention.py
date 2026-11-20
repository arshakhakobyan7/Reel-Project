"""Data retention (T-902; NFR-6, D-021, D-054): disk use stays bounded.

What is removed, from jobs that ended (Done, Failed or Cancelled) long enough ago:

- **working files** (``retention.working_files_days``, 0 = keep): the job's folder
  ``data/jobs/<id>/`` (audio, cleaned video, thumbnail, JSON files, job log). The video is on
  YouTube; a re-run from Cleaned makes them again. A Done job whose video is **not** on
  YouTube (dry-run, or the upload never went live) keeps its folder: the cleaned video there
  is the only copy (D-057);
- **transcripts** (``retention.transcripts_days``, 0 = keep): the raw and clean transcript
  rows; title, description and chapters stay;
- **unfinished uploads** older than ``working_files_days``;
- **source recordings**: only when ``retention.delete_source_recordings`` is true (D-021),
  only of Done jobs whose video is on YouTube, after ``source_recordings_days``, and only
  files inside a watched folder or ``data/recordings``.

Every removal is written as a ``cleanup`` event on the job (events themselves are never
deleted, so their ids are never reused; note from T-801). ``reel cleanup --dry-run`` only
lists. The runtime runs the cleanup once a day.
"""

import json
import logging
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from reel.config.loader import LoadedConfig
from reel.db import Database, Job, JobEvent, Transcript, utcnow
from reel.db.enums import ActorKind, JobStatus, PublicationStatus, TargetKind
from reel.ingest.upload import META, RECORDINGS, UPLOADS

log = logging.getLogger("reel.retention")

ENDED = (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)
EVENT = "cleanup"


@dataclass(frozen=True)
class Removal:
    kind: str  # working_files, transcripts, upload, source
    detail: str
    bytes: int = 0
    job_id: int | None = None
    path: Path | None = None


@dataclass
class Report:
    dry_run: bool
    removals: list[Removal] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # expired, but the only copy (D-057)

    @property
    def bytes(self) -> int:
        return sum(removal.bytes for removal in self.removals)


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _inside(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved.is_relative_to(root.resolve()) for root in roots)


def _ended_before(job: Job, limit: datetime) -> bool:
    moment = job.finished_at or job.updated_at
    return job.status in ENDED and moment is not None and moment < limit


def on_youtube(job: Job) -> bool:
    """True when the job's video was really uploaded to YouTube (not a dry-run)."""
    youtube = job.publication(TargetKind.YOUTUBE)
    return (
        youtube is not None
        and youtube.status is PublicationStatus.PUBLISHED
        and not youtube.dry_run
        and bool(youtube.external_id)
    )


def _only_copy(job: Job) -> bool:
    """A Done job whose cleaned video exists nowhere but here (D-057)."""
    return job.status is JobStatus.DONE and not on_youtube(job)


ONLY_COPY = "the video is not on YouTube, so this PC has the only copy"


def plan(
    db: Database, cfg: LoadedConfig, now: datetime, kept: list[str] | None = None
) -> list[Removal]:
    """What is due for removal. Expired items kept as the only copy go into ``kept``."""
    kept = [] if kept is None else kept
    settings, paths = cfg.settings.retention, cfg.paths
    removals: list[Removal] = []
    jobs_dir = paths.data_dir / "jobs"
    with db.transaction() as s:
        jobs = s.scalars(select(Job).where(Job.status.in_(ENDED)).order_by(Job.id)).all()
        if settings.working_files_days:
            limit = now - timedelta(days=settings.working_files_days)
            for job in jobs:
                folder = jobs_dir / str(job.id)
                if _ended_before(job, limit) and folder.is_dir() and any(folder.iterdir()):
                    if _only_copy(job):
                        kept.append(f"job {job.id} working files: {ONLY_COPY}")
                        continue
                    removals.append(Removal("working_files", f"job {job.id} working files",
                                            _size(folder), job.id, folder))  # fmt: skip
        if settings.transcripts_days:
            limit = now - timedelta(days=settings.transcripts_days)
            for job in jobs:
                if _ended_before(job, limit):
                    count = len(job.transcripts)
                    if count:
                        text = f"job {job.id}: {count} transcript(s)"
                        removals.append(Removal("transcripts", text, 0, job.id))
        if settings.delete_source_recordings:
            limit = now - timedelta(days=settings.source_recordings_days)
            roots = [*paths.watch_folders, paths.data_dir / RECORDINGS]
            for job in jobs:
                if job.status is not JobStatus.DONE or not _ended_before(job, limit):
                    continue
                recording = job.recording
                files = [Path(p["path"]) for p in (recording.media_info or {}).get("parts", [])]
                files = files or [Path(recording.source_path)]
                for path in files:
                    if not path.is_file():
                        continue
                    if not _inside(path, roots):
                        log.warning("job %s: %s is outside the watched folders; not deleted",
                                    job.id, path)  # fmt: skip
                        continue
                    if _only_copy(job):
                        kept.append(f"job {job.id} source {path.name}: {ONLY_COPY}")
                        continue
                    removals.append(Removal("source", f"job {job.id} source {path.name}",
                                            path.stat().st_size, job.id, path))  # fmt: skip
    if settings.working_files_days:
        limit = now - timedelta(days=settings.working_files_days)
        uploads = paths.data_dir / UPLOADS
        for folder in sorted(uploads.iterdir()) if uploads.is_dir() else []:
            try:
                meta = json.loads((folder / META).read_text(encoding="utf-8"))
                created = datetime.fromisoformat(meta["created_at"])
            except OSError, ValueError, KeyError:
                continue  # not an upload folder written by Reel: left alone
            if not meta.get("finished") and created < limit:
                removals.append(Removal("upload", f"unfinished upload {folder.name}",
                                        _size(folder), None, folder))  # fmt: skip
    return removals


def _events(db: Database, removals: list[Removal], now: datetime) -> None:
    by_job: dict[int, list[Removal]] = {}
    for removal in removals:
        if removal.job_id is not None:
            by_job.setdefault(removal.job_id, []).append(removal)
    if not by_job:
        return
    with db.transaction(immediate=True) as s:
        for job_id, done in by_job.items():
            freed = sum(r.bytes for r in done) / 1024**2
            s.add(JobEvent(
                job_id=job_id, at=now, kind=EVENT, actor_kind=ActorKind.SYSTEM,
                message=f"retention: removed {', '.join(r.kind for r in done)} ({freed:.0f} MB)",
                data={"removed": [r.detail for r in done]},
            ))  # fmt: skip


def _apply(db: Database, removal: Removal) -> None:
    if removal.kind == "transcripts":
        with db.transaction(immediate=True) as s:
            s.execute(delete(Transcript).where(Transcript.job_id == removal.job_id))
    elif removal.path is not None and removal.path.is_dir():
        shutil.rmtree(removal.path)
    elif removal.path is not None:
        removal.path.unlink(missing_ok=True)


def cleanup(
    db: Database, cfg: LoadedConfig, *, now: datetime | None = None, dry_run: bool = False
) -> Report:
    now = now or utcnow()
    report = Report(dry_run)
    for removal in plan(db, cfg, now, report.kept):
        if not dry_run:
            try:
                _apply(db, removal)
            except OSError as exc:  # e.g. a file open in a player: tried again tomorrow
                report.errors.append(f"{removal.detail}: {exc}")
                continue
        report.removals.append(removal)
    if not dry_run:
        _events(db, report.removals, now)
        if report.removals:
            log.info("retention removed %d item(s), %.0f MB", len(report.removals),
                     report.bytes / 1024**2)  # fmt: skip
    for error in report.errors:
        log.warning("retention could not remove %s", error)
    return report


def daily(db: Database, config, stop, *, first_delay: float = 600.0) -> Iterator[Report]:
    """Run the cleanup ``first_delay`` seconds after start, then every 24 hours, until
    ``stop`` is set (used by the runtime). Yields each report (for tests)."""
    wait = first_delay
    while not stop.wait(wait):
        try:
            yield cleanup(db, config())
        except Exception:
            log.exception("the daily cleanup failed; it runs again tomorrow")
        wait = 24 * 3600
