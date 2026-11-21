"""What the dashboard pages show, read from the database (T-803; FR-30, FR-12)."""

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import PureWindowsPath
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reel.db import PIPELINE, CutList, Job, Recording, utcnow
from reel.db.enums import JobStatus, Stage
from reel.web import texts

PAGE_SIZE = 50
NO_GROUP = "-"  # the group filter value for recordings without a group
REFRESH_SECONDS = 30  # the list page reloads itself (no JavaScript)


def clock_text(seconds: float | None) -> str:
    """``1:52:03`` or ``14:05``; empty for no value."""
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return ""
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def local_text(moment: datetime | None, zone: ZoneInfo) -> str:
    return moment.astimezone(zone).strftime("%Y-%m-%d %H:%M") if moment else ""


def recording_title(recording: Recording) -> str:
    """ "Python 12 · Դաս 14" as in the brief; the file name while the group is unknown."""
    name = PureWindowsPath(recording.source_path).name
    if not recording.group_name:
        return name
    if recording.lesson_number is None:
        return f"{recording.group_name} · {name}"
    return f"{recording.group_name} · Դաս {recording.lesson_number}"


@dataclass(frozen=True)
class Pill:
    label: str
    state: str  # a key of texts.PILL_TITLES

    @property
    def title(self) -> str:
        return texts.PILL_TITLES[self.state]


def pills(stage: Stage, status: JobStatus, *, waiting_retry: bool) -> list[Pill]:
    """One pill per pipeline stage: done, the current stage's state, or not started."""
    current = PIPELINE.index(stage)
    states = {
        JobStatus.RUNNING: "running",
        JobStatus.QUEUED: "retry" if waiting_retry else "queued",
        JobStatus.ON_HOLD: "held",
        JobStatus.FAILED: "failed",
        JobStatus.CANCELLED: "cancelled",
        JobStatus.DONE: "done",
    }
    result = []
    for index, each in enumerate(PIPELINE):
        if status is JobStatus.DONE or index < current:
            state = "done"
        elif index == current:
            state = states[status]
        else:
            state = "pending"
        result.append(Pill(texts.STAGE_LABELS[each], state))
    return result


@dataclass(frozen=True)
class ListRow:
    job_id: int
    title: str
    file_name: str
    status: JobStatus
    status_label: str
    pills: list[Pill]
    original: str
    final: str
    created: str
    updated: str
    problem: str  # the last error, or why the job waits for staff
    flags: list[tuple[str, str]]


@dataclass(frozen=True)
class ListPage:
    rows: list[ListRow]
    counts: dict[JobStatus, int]
    total: int  # all jobs
    matching: int  # jobs that pass the filters
    page: int
    pages: int
    groups: list[str]


def _clip(text: str | None, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def recordings_page(
    session: Session,
    zone: ZoneInfo,
    *,
    status: JobStatus | None = None,
    group: str | None = None,
    page: int = 1,
    configured_groups: tuple[str, ...] = (),
    owner: int | None = None,
) -> ListPage:
    """One page of the recordings list. ``owner``: only that account's uploads (T-1013)."""
    counts = {each: 0 for each in JobStatus}
    totals = select(Job.status, func.count()).join(Recording, Job.recording_id == Recording.id)
    if owner is not None:
        totals = totals.where(Recording.uploaded_by_id == owner)
    for each, number in session.execute(totals.group_by(Job.status)):
        counts[each] = number
    latest = (
        select(CutList.job_id, func.max(CutList.version).label("version"))
        .group_by(CutList.job_id)
        .subquery()
    )
    query = (
        select(Job, Recording, CutList.final_duration_seconds)
        .join(Recording, Job.recording_id == Recording.id)
        .outerjoin(latest, latest.c.job_id == Job.id)
        .outerjoin(CutList, (CutList.job_id == Job.id) & (CutList.version == latest.c.version))
    )
    if owner is not None:
        query = query.where(Recording.uploaded_by_id == owner)
    if status is not None:
        query = query.where(Job.status == status)
    if group == NO_GROUP:
        query = query.where(Recording.group_name.is_(None))
    elif group:
        query = query.where(Recording.group_name == group)
    matching = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    pages = max(1, math.ceil(matching / PAGE_SIZE))
    page = min(max(page, 1), pages)
    found = session.execute(
        query.order_by(Job.id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()
    now = utcnow()
    rows = []
    for job, recording, final in found:
        waiting = job.next_attempt_at is not None and job.next_attempt_at > now
        if job.status is JobStatus.ON_HOLD:
            problem = job.hold_message or job.hold_reason or ""
        elif job.status in {JobStatus.FAILED, JobStatus.QUEUED, JobStatus.RUNNING}:
            problem = job.last_error or ""
        else:
            problem = ""
        rows.append(
            ListRow(
                job.id,
                recording_title(recording),
                PureWindowsPath(recording.source_path).name,
                job.status,
                texts.STATUS_LABELS[job.status],
                pills(job.stage, job.status, waiting_retry=waiting),
                clock_text(recording.original_duration_seconds),
                clock_text(final),
                local_text(job.created_at, zone),
                local_text(job.updated_at, zone),
                _clip(problem),
                [(flag, texts.FLAG_LABELS.get(flag, flag)) for flag in job.flags or ()],
            )
        )
    named = select(Recording.group_name).where(Recording.group_name.is_not(None))
    if owner is not None:
        named, configured_groups = named.where(Recording.uploaded_by_id == owner), ()
    stored = session.scalars(named.distinct()).all()
    groups = sorted(set(stored) | set(configured_groups), key=str.casefold)
    return ListPage(rows, counts, sum(counts.values()), matching, page, pages, groups)
