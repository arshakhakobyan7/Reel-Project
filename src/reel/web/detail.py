"""The recording detail page's content (T-804; FR-30, FR-9, FR-32, UJ-3 … UJ-5)."""

import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from reel.db import (
    PIPELINE,
    ConsentCheck,
    Job,
    JobEvent,
    PublicationTarget,
    StaffUser,
    StageRun,
    utcnow,
)
from reel.db.enums import JobStatus, Stage
from reel.metrics import JobMetrics, load_job_metrics
from reel.retention import EVENT as CLEANUP_EVENT
from reel.retention import PROCESSED_VIDEO
from reel.web import texts
from reel.web.views import clock_text, local_text, pills, recording_title

EVENT_LIMIT = 300
BAR_WIDTH = 1000  # the SVG's own units; it is scaled to the page width


def safe_link(url: str | None) -> str | None:
    """Only http(s) links are shown as links (no ``javascript:`` and the like)."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return url if parts.scheme in {"http", "https"} and parts.netloc else None


@dataclass(frozen=True)
class Bar:
    """One cut-list segment drawn on the bar (SVG attributes, no inline styles: CSP)."""

    x: float
    width: float
    kept: bool
    reason: str
    span: str  # "0:00–14:05"


@dataclass(frozen=True)
class CutView:
    version: int
    original: str
    final: str
    bars: list[Bar]
    removed: list[tuple[str, str]]  # (reason, total time)


@dataclass
class Detail:
    job_id: int
    title: str
    file_name: str
    source_path: str
    status: JobStatus
    status_label: str
    stage: Stage
    pills: list
    hold_reason: str
    hold_label: str
    hold_message: str
    last_error: str
    next_attempt: str
    waiting_retry: bool
    flags: list[tuple[str, str]]
    group: str
    lesson: int | None
    date: str
    original: str
    final: str
    created: str
    finished: str
    runs: list[dict[str, Any]] = field(default_factory=list)
    cut: CutView | None = None
    metadata: dict[str, Any] | None = None
    consent: list[dict[str, Any]] = field(default_factory=list)
    publications: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    rerun_stages: list[tuple[str, str]] = field(default_factory=list)
    cost: JobMetrics | None = None
    budget_minutes: float = 0.0
    publish: bool = True  # False: a user's upload, only processed (T-1013)
    uploader: str = ""  # who uploaded it in the dashboard
    deleted: str = ""  # when the user's processed video was deleted (T-1014)

    @property
    def can_release(self) -> bool:
        return self.status is JobStatus.ON_HOLD

    @property
    def can_cancel(self) -> bool:
        return self.status in {
            JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.ON_HOLD, JobStatus.FAILED,
        }  # fmt: skip

    @property
    def can_identify(self) -> bool:
        return self.status in {
            JobStatus.QUEUED,
            JobStatus.ON_HOLD,
            JobStatus.FAILED,
            JobStatus.DONE,
        }

    @property
    def can_rerun(self) -> bool:
        return self.status is not JobStatus.RUNNING and not self.deleted

    @property
    def can_retry_now(self) -> bool:
        return self.status is JobStatus.QUEUED and self.waiting_retry


def _cut_view(job: Job) -> CutView | None:
    cut = job.latest_cut_list
    if cut is None or not cut.segments:
        return None
    total = max(cut.original_duration_seconds, cut.segments[-1]["end"]) or 1.0
    bars, removed = [], {}
    for segment in cut.segments:
        start, end = float(segment["start"]), float(segment["end"])
        kept = segment["action"] == "keep"
        reason = texts.cut_reason(str(segment.get("reason", "")))
        x = round(start / total * BAR_WIDTH, 2)
        width = round(max(end - start, 0.0) / total * BAR_WIDTH, 2)
        bars.append(Bar(x, width, kept, reason, f"{clock_text(start)}–{clock_text(end)}"))
        if not kept:
            removed[reason] = removed.get(reason, 0.0) + end - start
    return CutView(
        cut.version, clock_text(cut.original_duration_seconds),
        clock_text(cut.final_duration_seconds), bars,
        [(reason, clock_text(seconds)) for reason, seconds in
         sorted(removed.items(), key=lambda item: -item[1])],
    )  # fmt: skip


def _metadata(job: Job) -> dict[str, Any] | None:
    meta = job.video_metadata
    if meta is None:
        return None
    chapters = [(clock_text(float(c["start"])), c["title"]) for c in meta.chapters or ()]
    return {
        "title": meta.title,
        "summary": meta.summary,
        "description": meta.description,
        "chapters": chapters,
        "has_thumbnail": bool(meta.thumbnail_path) and Path(meta.thumbnail_path).is_file(),
    }


CLEANED_VIDEO = "cleaned.mp4"  # reel.stages.cleaning.CLEANED_FILE (not imported: heavy)
_NOT_IN_NAMES = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def video_path(job_id: int, data_dir: Path) -> Path | None:
    """The cleaned video of a job (D-057), when it exists."""
    path = data_dir / "jobs" / str(int(job_id)) / CLEANED_VIDEO
    return path if path.is_file() else None


def video_file_name(job: Job) -> str:
    """A Windows-safe download name from the title, e.g. "Python 12 · Lecture 14.mp4"."""
    title = job.video_metadata.title if job.video_metadata else ""
    name = " ".join(_NOT_IN_NAMES.sub(" ", title or "").split()).strip(" .")[:120]
    return f"{name or f'reel-{job.id}'}.mp4"


def thumbnail_path(job: Job, data_dir: Path) -> Path | None:
    """The thumbnail file, only if it is a file inside Reel's data folder."""
    meta = job.video_metadata
    if meta is None or not meta.thumbnail_path:
        return None
    path = Path(meta.thumbnail_path).resolve()
    if not path.is_relative_to(data_dir.resolve()) or not path.is_file():
        return None
    return path


def load_detail(
    session: Session,
    job_id: int,
    zone: ZoneInfo,
    *,
    cutoff: time = time(23, 59),
    budget_minutes: float = 240.0,
) -> Detail | None:
    job = session.get(Job, job_id)
    if job is None:
        return None
    recording = job.recording
    now = utcnow()
    waiting = job.next_attempt_at is not None and job.next_attempt_at > now
    cut = job.latest_cut_list
    detail = Detail(
        job.id, recording_title(recording), PureWindowsPath(recording.source_path).name,
        recording.source_path, job.status, texts.STATUS_LABELS[job.status], job.stage,
        pills(job.stage, job.status, waiting_retry=waiting), job.hold_reason or "",
        texts.HOLD_LABELS.get(job.hold_reason or "", job.hold_reason or ""),
        job.hold_message or "", job.last_error or "", local_text(job.next_attempt_at, zone),
        waiting, [(f, texts.FLAG_LABELS.get(f, f)) for f in job.flags or ()],
        recording.group_name or "", recording.lesson_number,
        recording.recording_date.isoformat() if recording.recording_date else "",
        clock_text(recording.original_duration_seconds),
        clock_text(cut.final_duration_seconds if cut else None),
        local_text(job.created_at, zone), local_text(job.finished_at, zone),
    )  # fmt: skip
    for run in session.scalars(
        select(StageRun).where(StageRun.job_id == job.id).order_by(StageRun.id)
    ):
        seconds = (run.finished_at - run.started_at).total_seconds() if run.finished_at else None
        detail.runs.append({
            "stage": texts.STAGE_LABELS[run.stage], "attempt": run.attempt,
            "status": run.status.value,
            "status_label": texts.RUN_LABELS.get(run.status.value, run.status.value),
            "started": local_text(run.started_at, zone), "seconds": clock_text(seconds),
            "error": run.error or "", "worker": run.worker or "",
        })  # fmt: skip
    detail.publish = recording.publish
    if recording.uploaded_by_id is not None:
        account = session.get(StaffUser, recording.uploaded_by_id)
        detail.uploader = (account.display_name or account.username) if account else ""
    detail.cut = _cut_view(job)
    detail.metadata = _metadata(job)
    for check in session.scalars(
        select(ConsentCheck).where(ConsentCheck.job_id == job.id).order_by(ConsentCheck.id.desc())
    ):
        statuses = [s.get("status") for s in check.students or ()]
        detail.consent.append({
            "at": local_text(check.checked_at, zone), "provider": check.provider,
            "result": check.result.value,
            "result_label": texts.CONSENT_LABELS.get(check.result.value, check.result.value),
            "granted": statuses.count("granted"), "denied": statuses.count("denied"),
            "unknown": statuses.count("unknown"), "message": check.message or "",
            "released_by": check.released_by or "",
            "released_at": local_text(check.released_at, zone),
            "release_note": check.release_note or "",
        })  # fmt: skip
    rows = {row.target.value: row for row in session.scalars(
        select(PublicationTarget).where(PublicationTarget.job_id == job.id)
    )}  # fmt: skip
    for target in ("youtube", "slack", "classroom", "library"):
        row = rows.get(target)
        detail.publications.append({
            "target": texts.TARGET_LABELS[target],
            "status": row.status.value if row else "",
            "status_label": texts.PUBLICATION_LABELS.get(row.status.value, "") if row else "Դեռ չէ",
            "dry_run": bool(row and row.dry_run),
            "url": safe_link(row.url) if row else None,
            "external_id": (row.external_id or "") if row else "",
            "attempts": row.attempts if row else 0,
            "error": (row.last_error or "") if row else "",
            "skipped": str((row.details or {}).get("skipped", "")) if row else "",
        })  # fmt: skip
    for event in session.scalars(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.id.desc())
        .limit(EVENT_LIMIT)
    ):  # fmt: skip
        detail.events.append({
            "at": local_text(event.at, zone),
            "kind": texts.EVENT_LABELS.get(event.kind, event.kind),
            "actor": texts.ACTOR_LABELS.get(event.actor_kind.value, event.actor_kind.value),
            "name": event.actor_name or "",
            "stage": texts.STAGE_LABELS.get(event.stage, "") if event.stage else "",
            "message": event.message,
        })  # fmt: skip
    for event in session.scalars(
        select(JobEvent).where(JobEvent.job_id == job.id, JobEvent.kind == CLEANUP_EVENT)
        .order_by(JobEvent.id.desc())
    ):  # fmt: skip
        kinds = (event.data or {}).get("kinds") or ()
        if PROCESSED_VIDEO in kinds or "uploaded_files" in kinds:
            detail.deleted = local_text(event.at, zone)
            break
    (detail.cost,) = load_job_metrics(
        session, [job], zone=zone, cutoff=cutoff, budget_minutes=budget_minutes
    )
    detail.budget_minutes = budget_minutes
    reached = PIPELINE.index(job.stage)
    detail.rerun_stages = [(s.value, texts.STAGE_LABELS[s]) for s in PIPELINE[: reached + 1]]
    return detail
