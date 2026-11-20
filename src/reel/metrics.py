"""Processing metrics, compute "cost" and the time budget (T-805; NFR-7, NFR-8, M-1 … M-6,
D-020, D-050).

Local Whisper and Ollama cost no money (D-020), so the cost of a recording is its processing
time: the stage runs' wall-clock time, split into compute for Whisper (the transcribe
stage), the LLM (the clean and chapters stages, from the token meter) and rendering, plus the
LLM tokens.

The success metrics, over the jobs created in a period:
- failures per stage (failed attempts, and jobs that ended Failed there);
- the median time from the file to the links: from the job's creation (the file was found
  and stable) to Done;
- the same-evening rate: Done jobs finished on the day the file arrived, before
  ``schedule.same_evening_cutoff`` (local time, Q-3);
- the share processed without staff: Done jobs with no staff event;
- the budget: jobs whose processing took longer than ``budget.max_processing_minutes``.
"""

import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from reel.db import PIPELINE, Job, JobEvent, StageRun, utcnow
from reel.db.enums import ActorKind, JobStatus, Stage, StageRunStatus

BUDGET_FLAG = "budget_exceeded"
BUDGET_EVENT = "budget_exceeded"


def _run_seconds(run: StageRun) -> float:
    if run.finished_at is None:
        return 0.0
    seconds = (run.finished_at - run.started_at).total_seconds()
    # Time the PC was asleep during the run is not work (D-056).
    return max(seconds - _number((run.metrics or {}).get("suspended_seconds")), 0.0)


def _number(value: Any) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


@dataclass
class JobMetrics:
    job_id: int
    status: str
    stage_seconds: dict[str, float] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    failed_attempts: dict[str, int] = field(default_factory=dict)
    processing_seconds: float = 0.0
    whisper_seconds: float = 0.0
    render_seconds: float = 0.0
    llm_seconds: float = 0.0
    llm_calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    file_to_links_minutes: float | None = None
    same_evening: bool | None = None
    staff_touched: bool = False
    over_budget: bool = False

    @property
    def processing_minutes(self) -> float:
        return self.processing_seconds / 60


def _job_metrics(
    job: Job,
    runs: list[StageRun],
    staff_touched: bool,
    *,
    zone: ZoneInfo,
    cutoff: time,
    budget_minutes: float,
) -> JobMetrics:
    result = JobMetrics(job.id, job.status.value, staff_touched=staff_touched)
    for run in runs:
        stage = run.stage.value
        seconds = _run_seconds(run)
        result.stage_seconds[stage] = result.stage_seconds.get(stage, 0.0) + seconds
        result.attempts[stage] = result.attempts.get(stage, 0) + 1
        if run.status is StageRunStatus.FAILED:
            result.failed_attempts[stage] = result.failed_attempts.get(stage, 0) + 1
        result.processing_seconds += seconds
        metrics = run.metrics or {}
        if run.stage is Stage.TRANSCRIBE:
            result.whisper_seconds += seconds
        if run.stage is Stage.CLEAN:
            result.render_seconds += _number((metrics.get("seconds") or {}).get("render"))
        llm = metrics.get("llm")
        if isinstance(llm, dict):
            result.llm_seconds += _number(llm.get("seconds"))
            result.llm_calls += int(_number(llm.get("calls")))
            result.prompt_tokens += int(_number(llm.get("prompt_tokens")))
            result.output_tokens += int(_number(llm.get("output_tokens")))
    if job.status is JobStatus.DONE and job.finished_at is not None:
        result.file_to_links_minutes = (job.finished_at - job.created_at).total_seconds() / 60
        arrived, finished = job.created_at.astimezone(zone), job.finished_at.astimezone(zone)
        result.same_evening = finished.date() == arrived.date() and finished.time() <= cutoff
    result.over_budget = result.processing_minutes > budget_minutes
    return result


@dataclass
class Summary:
    days: int  # 0 = all time
    jobs: int
    by_status: dict[str, int]
    failed_attempts_by_stage: dict[str, int]
    failed_jobs_by_stage: dict[str, int]
    done: int
    median_minutes_to_links: float | None
    same_evening: int
    same_evening_rate: float | None
    without_staff: int
    without_staff_rate: float | None
    budget_minutes: float
    over_budget: int
    median_processing_minutes: float | None
    whisper_hours: float
    render_hours: float
    llm_hours: float
    llm_tokens: int
    jobs_detail: list[JobMetrics]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["jobs_detail"] = [
            {**asdict(job), "processing_minutes": round(job.processing_minutes, 2)}
            for job in self.jobs_detail
        ]
        return data


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def load_job_metrics(
    session: Session, jobs: list[Job], *, zone: ZoneInfo, cutoff: time, budget_minutes: float
) -> list[JobMetrics]:
    ids = [job.id for job in jobs]
    runs: dict[int, list[StageRun]] = defaultdict(list)
    touched: set[int] = set()
    for chunk in range(0, len(ids), 500):  # SQLite limits the number of parameters
        part = ids[chunk : chunk + 500]
        for run in session.scalars(
            select(StageRun).where(StageRun.job_id.in_(part)).order_by(StageRun.id)
        ):
            runs[run.job_id].append(run)
        touched.update(
            session.scalars(
                select(JobEvent.job_id)
                .where(JobEvent.job_id.in_(part), JobEvent.actor_kind == ActorKind.STAFF)
                .distinct()
            )
        )
    return [
        _job_metrics(job, runs[job.id], job.id in touched, zone=zone, cutoff=cutoff,
                     budget_minutes=budget_minutes)
        for job in jobs
    ]  # fmt: skip


def summarize(
    session: Session,
    *,
    days: int,
    zone: ZoneInfo,
    cutoff: time,
    budget_minutes: float,
    now: datetime | None = None,
) -> Summary:
    query = select(Job).order_by(Job.id)
    if days > 0:
        query = query.where(Job.created_at >= (now or utcnow()) - timedelta(days=days))
    jobs = list(session.scalars(query))
    details = load_job_metrics(session, jobs, zone=zone, cutoff=cutoff,
                               budget_minutes=budget_minutes)  # fmt: skip
    by_status = {status.value: 0 for status in JobStatus}
    failed_jobs = {stage.value: 0 for stage in PIPELINE}
    for job in jobs:
        by_status[job.status.value] += 1
        if job.status is JobStatus.FAILED:
            failed_jobs[job.stage.value] += 1
    failed_attempts = {stage.value: 0 for stage in PIPELINE}
    for detail in details:
        for stage, count in detail.failed_attempts.items():
            failed_attempts[stage] += count
    done = [d for d in details if d.status == JobStatus.DONE.value]
    to_links = [d.file_to_links_minutes for d in done if d.file_to_links_minutes is not None]
    evening = sum(1 for d in done if d.same_evening)
    alone = sum(1 for d in done if not d.staff_touched)
    finished = [d for d in details if d.status in {JobStatus.DONE.value, JobStatus.FAILED.value}]
    return Summary(
        days=days,
        jobs=len(jobs),
        by_status=by_status,
        failed_attempts_by_stage=failed_attempts,
        failed_jobs_by_stage=failed_jobs,
        done=len(done),
        median_minutes_to_links=_median(to_links),
        same_evening=evening,
        same_evening_rate=_rate(evening, len(done)),
        without_staff=alone,
        without_staff_rate=_rate(alone, len(done)),
        budget_minutes=budget_minutes,
        over_budget=sum(1 for d in details if d.over_budget),
        median_processing_minutes=_median([d.processing_minutes for d in finished]),
        whisper_hours=round(sum(d.whisper_seconds for d in details) / 3600, 3),
        render_hours=round(sum(d.render_seconds for d in details) / 3600, 3),
        llm_hours=round(sum(d.llm_seconds for d in details) / 3600, 3),
        llm_tokens=sum(d.prompt_tokens + d.output_tokens for d in details),
        jobs_detail=details,
    )


def check_budget(
    session: Session, job_id: int, budget_minutes: float, *, now: datetime | None = None
) -> float | None:
    """After a stage attempt: if the job's processing time is over the budget for the first
    time, flag it and write a ``budget_exceeded`` event (staff are notified from it).
    Returns the minutes then, else None."""
    job = session.get(Job, job_id)
    if job is None or BUDGET_FLAG in (job.flags or ()):
        return None
    runs = session.scalars(select(StageRun).where(StageRun.job_id == job_id)).all()
    minutes = sum(_run_seconds(run) for run in runs) / 60
    if minutes <= budget_minutes:
        return None
    job.add_flag(BUDGET_FLAG)
    session.add(
        JobEvent(
            job_id=job_id,
            at=now or utcnow(),
            kind=BUDGET_EVENT,
            actor_kind=ActorKind.SYSTEM,
            stage=job.stage,
            message=f"processing has taken {minutes:.0f} min, more than the budget of "
            f"{budget_minutes:g} min",
            data={"minutes": round(minutes, 1), "budget": budget_minutes, "flags": list(job.flags)},
        )  # fmt: skip
    )
    return minutes
