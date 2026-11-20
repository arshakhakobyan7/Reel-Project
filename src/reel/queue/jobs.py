"""Persistent job queue and job state machine (T-102; ARCHITECTURE.md §4; D-028).

::

    queued ──claim──► running ──complete──► queued at the next stage … ──► done
                        │ fail, retryable, attempts left ──► queued after a backoff delay
                        │ fail, fatal or no attempts left ──► failed
                        │ hold(reason) ──► on_hold ──release──► queued (same or next stage)
                        │ lease expired (worker crashed) ──► queued after backoff, or failed
                        └ interrupt (worker stopping) ──► queued (attempt not counted)
    rerun(stage): queued / on_hold / failed / done / cancelled ──► queued at that stage
    cancel: queued / running / on_hold / failed ──► cancelled

Each operation is one ``BEGIN IMMEDIATE`` transaction, so two workers (threads or separate
processes) can never claim the same job. Every transition writes a JobEvent. Every stage
attempt has a StageRun; the transition that ends the attempt closes it.
"""

import logging
import math
import re
from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from reel.config.schema import WorkerSettings
from reel.db import (
    PIPELINE,
    ActorKind,
    ConsentCheck,
    Database,
    EventKind,
    Job,
    JobEvent,
    JobStatus,
    Recording,
    Stage,
    StageRun,
    StageRunStatus,
    next_stage,
    utcnow,
)

log = logging.getLogger("reel.queue")

MAX_TEXT = 4000  # longer error texts are cut; the full traceback is in the job log
# The identification flags and hold (T-203, T-504); kept equal to
# reel.ingest.identify.FLAGS by a test (imported here, it would be a cycle).
IDENTIFICATION_FLAGS = frozenset(
    {"unknown_group", "missing_lesson_number", "ambiguous_lesson_number", "missing_date"}
)
IDENTIFICATION_HOLD = "identification"
MAX_RETRY_AFTER = 2 * 86400.0  # a stage cannot ask to wait longer than two days
_NAME_MAX = 200
_REASON = re.compile(r"[a-z0-9_]{1,64}")

Actor = tuple[ActorKind, str | None]
SYSTEM: Actor = (ActorKind.SYSTEM, None)


class QueueError(Exception):
    """A queue operation could not be done."""


class JobNotFound(QueueError):
    pass


class InvalidTransition(QueueError):
    """The action is not allowed in the job's current state. Nothing was changed."""


class LeaseLost(QueueError):
    """This worker attempt no longer holds the job.

    Staff cancelled the job, or the lease expired and the job went back to the queue. The
    worker must stop the stage and discard its result.
    """


@dataclass(frozen=True)
class RetryPolicy:
    """Automatic retries (FR-31): wait ``base × factor^(attempt − 1)``, at most ``max``."""

    max_attempts: int = 3
    backoff_base_seconds: float = 60.0
    backoff_factor: float = 4.0
    backoff_max_seconds: float = 3600.0

    @classmethod
    def from_settings(cls, workers: WorkerSettings) -> RetryPolicy:
        return cls(
            max_attempts=workers.max_attempts,
            backoff_base_seconds=workers.backoff_base_seconds,
            backoff_factor=workers.backoff_factor,
            backoff_max_seconds=workers.backoff_max_seconds,
        )

    def delay(self, attempt: int) -> timedelta:
        """How long to wait after failed attempt number ``attempt`` (1, 2, ...)."""
        if attempt < 1:
            raise ValueError("attempt numbers start at 1")
        try:
            seconds = self.backoff_base_seconds * self.backoff_factor ** (attempt - 1)
        except OverflowError:
            seconds = math.inf
        return timedelta(seconds=min(seconds, self.backoff_max_seconds))


@dataclass(frozen=True)
class JobSnapshot:
    """The state of a job right after an operation. Plain data, safe to pass around."""

    id: int
    recording_id: int
    stage: Stage
    status: JobStatus
    attempts: int
    next_attempt_at: datetime | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    hold_reason: str | None
    hold_message: str | None
    last_error: str | None
    finished_at: datetime | None
    version: int

    @classmethod
    def of(cls, job: Job) -> JobSnapshot:
        return cls(
            id=job.id,
            recording_id=job.recording_id,
            stage=job.stage,
            status=job.status,
            attempts=job.attempts,
            next_attempt_at=job.next_attempt_at,
            lease_owner=job.lease_owner,
            lease_expires_at=job.lease_expires_at,
            hold_reason=job.hold_reason,
            hold_message=job.hold_message,
            last_error=job.last_error,
            finished_at=job.finished_at,
            version=job.version,
        )


@dataclass(frozen=True)
class Claim:
    """One attempt of one stage, leased to one worker. Pass it back to every worker call."""

    job_id: int
    recording_id: int
    stage: Stage
    attempt: int
    stage_run_id: int
    worker: str
    lease_expires_at: datetime


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def _name(value: str | None, what: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > _NAME_MAX:
        raise ValueError(f"{what} must be 1-{_NAME_MAX} characters")
    return value


def _staff(by: str) -> Actor:
    return (ActorKind.STAFF, _name(by, "staff user"))


def _seconds(delta: timedelta) -> int:
    return round(delta.total_seconds())


def _record(
    session: Session,
    job: Job,
    kind: EventKind,
    actor: Actor,
    now: datetime,
    message: str,
    *,
    stage: Stage | None = None,
    **data: Any,
) -> None:
    stage = stage or job.stage
    session.add(
        JobEvent(
            job_id=job.id,
            at=now,
            kind=kind,
            actor_kind=actor[0],
            actor_name=actor[1],
            stage=stage,
            message=message,
            data=data,
        )
    )
    log.info(message, extra={"job_id": job.id, "stage": str(stage), "event": str(kind)})


def _close(
    run: StageRun,
    status: StageRunStatus,
    now: datetime,
    *,
    error: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> None:
    run.status = status
    run.finished_at = now
    if error:
        run.error = _clip(error)
    if metrics:
        run.metrics = {**run.metrics, **metrics}


def _drop_lease(job: Job) -> None:
    job.lease_owner = None
    job.lease_expires_at = None


def _load(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise JobNotFound(f"job {job_id} does not exist")
    return job


def _require(job: Job, action: str, *allowed: JobStatus) -> None:
    if job.status not in allowed:
        names = ", ".join(status.label.lower() for status in allowed)
        raise InvalidTransition(
            f"cannot {action} job {job.id}: it is {job.status.label.lower()} "
            f"(possible when it is {names})"
        )


def _snapshot(session: Session, job: Job) -> JobSnapshot:
    session.flush()  # the row version changes on flush
    return JobSnapshot.of(job)


def create_job(
    session: Session,
    recording: Recording | int,
    *,
    stage: Stage = Stage.IMPORT,
    by: str | None = None,
    now: datetime | None = None,
) -> Job:
    """Create the job of a recording inside the caller's transaction.

    The folder watcher (T-202) uses this to store a file, its recording and its job in one
    transaction. Without ``by`` the job counts as created by the system.
    """
    now = now or utcnow()
    actor = _staff(by) if by is not None else SYSTEM
    if isinstance(recording, int):
        found = session.get(Recording, recording)
        if found is None:
            raise QueueError(f"recording {recording} does not exist")
        recording = found
    if recording.id is not None:
        existing = session.scalar(select(Job.id).where(Job.recording_id == recording.id))
        if existing is not None:
            raise InvalidTransition(f"recording {recording.id} already has job {existing}")
    stage = Stage(stage)
    job = Job(
        recording=recording,
        stage=stage,
        status=JobStatus.QUEUED,
        attempts=0,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    _record(session, job, EventKind.CREATED, actor, now, f"queued for {stage.label}")
    return job


class JobQueue:
    """Queue operations on one database. Safe to share between threads."""

    def __init__(
        self,
        db: Database,
        *,
        policy: RetryPolicy | None = None,
        lease_seconds: float = 300.0,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.db = db
        self.policy = policy or RetryPolicy()
        self.lease_seconds = lease_seconds
        self._clock = clock

    @classmethod
    def from_settings(
        cls, db: Database, workers: WorkerSettings, *, clock: Callable[[], datetime] = utcnow
    ) -> JobQueue:
        queue = cls(db, clock=clock)
        queue.apply_settings(workers)
        return queue

    def apply_settings(self, workers: WorkerSettings) -> None:
        """Use changed settings from the next operation on (NFR-9: no restart needed)."""
        self.policy = RetryPolicy.from_settings(workers)
        self.lease_seconds = workers.lease_seconds

    # --- helpers -------------------------------------------------------------------------

    @contextmanager
    def _transaction(self) -> Iterator[tuple[Session, datetime]]:
        with self.db.transaction(immediate=True) as session:
            session.connection()  # BEGIN IMMEDIATE: wait for the write lock before reading
            now = self._clock()
            if now.utcoffset() is None:
                raise ValueError("the clock must return time-zone-aware datetimes")
            yield session, now

    def _lease_until(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.lease_seconds)

    def _owned(self, session: Session, claim: Claim) -> tuple[Job, StageRun]:
        job = session.get(Job, claim.job_id)
        run = session.get(StageRun, claim.stage_run_id)
        if (
            job is None
            or run is None
            or run.job_id != job.id
            or run.status is not StageRunStatus.RUNNING
            or job.status is not JobStatus.RUNNING
            or job.lease_owner != claim.worker
        ):
            raise LeaseLost(
                f"job {claim.job_id}: attempt {claim.attempt} of {claim.stage.label} no longer "
                "holds the job (it was cancelled, or its lease expired)"
            )
        return job, run

    def _retry_or_fail(
        self,
        job: Job,
        now: datetime,
        *,
        retryable: bool = True,
        retry_after: float | None = None,
    ) -> bool:
        """After a failed attempt: schedule the next try (True) or fail the job (False).
        ``retry_after`` (seconds) makes the wait at least that long."""
        if retryable and job.attempts < self.policy.max_attempts:
            job.status = JobStatus.QUEUED
            delay = self.policy.delay(max(job.attempts, 1))
            if retry_after is not None and math.isfinite(retry_after) and retry_after > 0:
                delay = max(delay, timedelta(seconds=min(retry_after, MAX_RETRY_AFTER)))
            job.next_attempt_at = now + delay
            return True
        job.status = JobStatus.FAILED
        job.finished_at = now
        return False

    def _recover(self, session: Session, now: datetime, keep: Collection[str] = ()) -> list[Job]:
        query = select(Job).where(
            Job.status == JobStatus.RUNNING,
            or_(Job.lease_expires_at.is_(None), Job.lease_expires_at < now),
        )
        if keep:
            alive = Job.lease_owner.not_in(list(keep))
            query = query.where(or_(Job.lease_owner.is_(None), alive))
        expired = session.scalars(query.order_by(Job.id)).all()
        for job in expired:
            owner = job.lease_owner
            # The attempt ended when its lease ran out, even if this runs much later (e.g.
            # the only worker crashed and was restarted an hour later): backoff counts
            # from then.
            ended = min(job.lease_expires_at or now, now)
            reason = f"lease expired: worker {owner} stopped responding"
            runs = session.scalars(
                select(StageRun).where(
                    StageRun.job_id == job.id, StageRun.status == StageRunStatus.RUNNING
                )
            ).all()
            for run in runs:
                _close(run, StageRunStatus.INTERRUPTED, ended, error=reason)
            _drop_lease(job)
            job.last_error = reason
            job.updated_at = now
            attempt = job.attempts
            if self._retry_or_fail(job, ended):
                wait = _seconds(job.next_attempt_at - now)
                outcome = f"next try in {wait} s" if wait > 0 else "can be tried again now"
            else:
                outcome = f"all {self.policy.max_attempts} attempts used: failed"
            _record(
                session,
                job,
                EventKind.LEASE_EXPIRED,
                SYSTEM,
                now,
                f"{job.stage.label} attempt {attempt}: {reason}; {outcome}",
                worker=owner,
                attempt=attempt,
                status=str(job.status),
            )
        return list(expired)

    # --- creating and reading ------------------------------------------------------------

    def enqueue(
        self, recording_id: int, *, stage: Stage = Stage.IMPORT, by: str | None = None
    ) -> JobSnapshot:
        """Create the job of a recording, queued at ``stage``."""
        with self._transaction() as (session, now):
            job = create_job(session, recording_id, stage=stage, by=by, now=now)
            return _snapshot(session, job)

    def get(self, job_id: int) -> JobSnapshot:
        with self.db.transaction() as session:
            return JobSnapshot.of(_load(session, job_id))

    def active_count(self, stages: Collection[Stage] | None = None) -> int:
        """Queued (also waiting for a retry) or running jobs, optionally at some stages."""
        query = (
            select(func.count())
            .select_from(Job)
            .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        )
        if stages is not None:
            query = query.where(Job.stage.in_([Stage(stage) for stage in stages]))
        with self.db.transaction() as session:
            return session.scalar(query) or 0

    # --- worker side ---------------------------------------------------------------------

    def claim(
        self,
        worker: str,
        *,
        stages: Collection[Stage] | None = None,
        heavy_stages: Collection[Stage] = (),
        heavy_limit: int | None = None,
        keep: Collection[str] = (),
    ) -> Claim | None:
        """Lease the oldest job that is due, or return None when there is none.

        Expired leases are recovered first. ``stages`` limits the stages this worker can
        run. When ``heavy_limit`` jobs already run a stage from ``heavy_stages``, only
        other stages are claimed; the check and the claim are one transaction, so the limit
        holds across processes (used by the worker runtime, T-103). Leases of the workers
        in ``keep`` are not recovered: the caller knows they are alive, for example after
        the PC slept longer than a lease (D-056).
        """
        worker = _name(worker, "worker")
        with self._transaction() as (session, now):
            self._recover(session, now, keep)
            query = select(Job).where(
                Job.status == JobStatus.QUEUED,
                or_(Job.next_attempt_at.is_(None), Job.next_attempt_at <= now),
            )
            if stages is not None:
                query = query.where(Job.stage.in_([Stage(stage) for stage in stages]))
            heavy = [Stage(stage) for stage in heavy_stages]
            if heavy and heavy_limit is not None:
                running_heavy = session.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.status == JobStatus.RUNNING, Job.stage.in_(heavy))
                )
                if running_heavy >= heavy_limit:
                    query = query.where(Job.stage.not_in(heavy))
            job = session.scalars(query.order_by(Job.id).limit(1)).first()
            if job is None:
                return None
            job.status = JobStatus.RUNNING
            job.attempts += 1
            job.lease_owner = worker
            job.lease_expires_at = self._lease_until(now)
            job.next_attempt_at = None
            job.started_at = job.started_at or now
            job.updated_at = now
            run = StageRun(
                job_id=job.id,
                stage=job.stage,
                attempt=job.attempts,
                status=StageRunStatus.RUNNING,
                worker=worker,
                started_at=now,
            )
            session.add(run)
            session.flush()
            _record(
                session,
                job,
                EventKind.STARTED,
                (ActorKind.WORKER, worker),
                now,
                f"{job.stage.label}: attempt {job.attempts} started by {worker}",
                attempt=job.attempts,
            )
            return Claim(
                job_id=job.id,
                recording_id=job.recording_id,
                stage=job.stage,
                attempt=job.attempts,
                stage_run_id=run.id,
                worker=worker,
                lease_expires_at=job.lease_expires_at,
            )

    def heartbeat(self, claim: Claim, *, progress: dict[str, Any] | None = None) -> datetime:
        """Extend the lease of a running stage and save its latest progress, if any.
        Returns the new expiry time."""
        with self._transaction() as (session, now):
            job, run = self._owned(session, claim)
            job.lease_expires_at = self._lease_until(now)
            if progress is not None and run.metrics.get("progress") != progress:
                run.metrics = {**run.metrics, "progress": progress}
            return job.lease_expires_at

    def complete(
        self, claim: Claim, *, metrics: dict[str, Any] | None = None, message: str = ""
    ) -> JobSnapshot:
        """The stage succeeded: queue the next stage, or mark the job done."""
        with self._transaction() as (session, now):
            job, run = self._owned(session, claim)
            _close(run, StageRunStatus.SUCCEEDED, now, metrics=metrics)
            _drop_lease(job)
            job.last_error = None
            job.updated_at = now
            following = next_stage(job.stage)
            if following is None:
                job.status = JobStatus.DONE
                job.finished_at = now
                kind, text = EventKind.DONE, f"{run.stage.label} finished; all stages done"
            else:
                job.stage = following
                job.status = JobStatus.QUEUED
                job.attempts = 0
                kind = EventKind.STAGE_COMPLETED
                text = f"{run.stage.label} finished; next: {following.label}"
            if message:
                text = f"{text}. {message}"
            _record(
                session,
                job,
                kind,
                (ActorKind.WORKER, claim.worker),
                now,
                text,
                stage=run.stage,
                attempt=run.attempt,
                seconds=round((now - run.started_at).total_seconds(), 3),
                flags=list(job.flags),  # as they were then, for staff notifications (T-801)
            )
            return _snapshot(session, job)

    def fail(
        self,
        claim: Claim,
        error: str,
        *,
        retryable: bool = True,
        metrics: dict[str, Any] | None = None,
        retry_after: float | None = None,
    ) -> JobSnapshot:
        """The stage failed: try again after a delay while attempts are left, else fail.
        ``retry_after``: wait at least this many seconds (e.g. until a quota resets)."""
        error = _clip(error or "") or "unknown error"
        with self._transaction() as (session, now):
            job, run = self._owned(session, claim)
            _close(run, StageRunStatus.FAILED, now, error=error, metrics=metrics)
            _drop_lease(job)
            job.last_error = error
            job.updated_at = now
            if self._retry_or_fail(job, now, retryable=retryable, retry_after=retry_after):
                wait = _seconds(job.next_attempt_at - now)
                kind = EventKind.RETRY_SCHEDULED
                text = (
                    f"{run.stage.label} failed (attempt {run.attempt} of "
                    f"{self.policy.max_attempts}); next try in {wait} s: {error}"
                )
            else:
                why = (
                    f"all {self.policy.max_attempts} attempts used"
                    if retryable
                    else "not retryable"
                )
                kind, text = EventKind.FAILED, f"{run.stage.label} failed ({why}): {error}"
            _record(
                session,
                job,
                kind,
                (ActorKind.WORKER, claim.worker),
                now,
                text,
                stage=run.stage,
                attempt=run.attempt,
                retryable=retryable,
            )
            return _snapshot(session, job)

    def hold(
        self,
        claim: Claim,
        reason: str,
        message: str = "",
        *,
        metrics: dict[str, Any] | None = None,
    ) -> JobSnapshot:
        """The stage needs a staff decision: put the job on hold (e.g. consent, FR-21).

        ``reason`` is a short code such as ``consent_opted_out``; ``message`` explains it.
        """
        if not _REASON.fullmatch(reason or ""):
            raise ValueError("hold reason must be 1-64 characters a-z, 0-9 or _")
        with self._transaction() as (session, now):
            job, run = self._owned(session, claim)
            _close(run, StageRunStatus.HELD, now, metrics=metrics)
            _drop_lease(job)
            job.status = JobStatus.ON_HOLD
            job.hold_reason = reason
            job.hold_message = _clip(message) if message.strip() else None
            job.updated_at = now
            _record(
                session,
                job,
                EventKind.HELD,
                (ActorKind.WORKER, claim.worker),
                now,
                f"{run.stage.label} put the job on hold: {job.hold_message or reason}",
                attempt=run.attempt,
                reason=reason,
            )
            return _snapshot(session, job)

    def interrupt(self, claim: Claim, reason: str = "the worker is stopping") -> JobSnapshot:
        """Give the job back because the worker stops; the attempt is not counted."""
        with self._transaction() as (session, now):
            job, run = self._owned(session, claim)
            _close(run, StageRunStatus.INTERRUPTED, now, error=reason)
            _drop_lease(job)
            job.attempts = max(job.attempts - 1, 0)
            job.status = JobStatus.QUEUED
            job.next_attempt_at = None
            job.updated_at = now
            _record(
                session,
                job,
                EventKind.INTERRUPTED,
                (ActorKind.WORKER, claim.worker),
                now,
                f"{run.stage.label} attempt {run.attempt} interrupted: {reason}; queued again",
                attempt=run.attempt,
            )
            return _snapshot(session, job)

    def recover_expired(self) -> list[JobSnapshot]:
        """Give back jobs whose worker stopped renewing its lease (it crashed or hung).

        This counts as a failed attempt. ``claim`` does this too, before claiming.
        """
        with self._transaction() as (session, now):
            return [_snapshot(session, job) for job in self._recover(session, now)]

    # --- staff side ----------------------------------------------------------------------

    def release(
        self, job_id: int, by: str, *, advance: bool = False, note: str = ""
    ) -> JobSnapshot:
        """Release a job on hold (UJ-3).

        ``advance=False`` runs the held stage again (e.g. after staff set the group).
        ``advance=True`` accepts the stage as passed and continues with the next one
        (e.g. staff checked the video after a consent hold). Accepting a consent hold also
        writes who released it, when and why on the job's latest consent check (audit, T-602).
        """
        actor = _staff(by)
        with self._transaction() as (session, now):
            job = _load(session, job_id)
            _require(job, "release", JobStatus.ON_HOLD)
            held_stage, reason = job.stage, job.hold_reason
            job.hold_reason = None
            job.hold_message = None
            job.attempts = 0
            job.next_attempt_at = None
            job.updated_at = now
            following = next_stage(held_stage) if advance else held_stage
            if following is None:
                job.status = JobStatus.DONE
                job.finished_at = now
                outcome = "all stages done"
            else:
                job.stage = following
                job.status = JobStatus.QUEUED
                outcome = f"continues with {following.label}"
            if advance and held_stage is Stage.CONSENT:
                check = session.scalars(
                    select(ConsentCheck)
                    .where(ConsentCheck.job_id == job.id)
                    .order_by(ConsentCheck.id.desc())
                    .limit(1)
                ).first()
                if check is not None:
                    check.released_by, check.released_at = actor[1], now
                    check.release_note = note or None
            if advance:
                text = f"released by {actor[1]}: {held_stage.label} accepted; {outcome}"
            else:
                text = f"released by {actor[1]}: {held_stage.label} runs again"
            _record(
                session,
                job,
                EventKind.RELEASED,
                actor,
                now,
                text,
                stage=held_stage,
                reason=reason,
                advance=advance,
                note=note,
            )
            return _snapshot(session, job)

    def discard(
        self, claim: Claim, message: str, *, metrics: dict[str, Any] | None = None
    ) -> JobSnapshot:
        """The stage decided that the job must not go on (e.g. a duplicate recording, FR-4):
        cancel it. Unlike ``cancel`` the decision comes from the worker, not from staff."""
        with self._transaction() as (session, now):
            job, run = self._owned(session, claim)
            _close(run, StageRunStatus.CANCELLED, now, error=message, metrics=metrics)
            _drop_lease(job)
            job.status = JobStatus.CANCELLED
            job.next_attempt_at = None
            job.last_error = _clip(message)
            job.finished_at = now
            job.updated_at = now
            _record(
                session,
                job,
                EventKind.CANCELLED,
                (ActorKind.WORKER, claim.worker),
                now,
                f"{run.stage.label} cancelled the job: {_clip(message)}",
                attempt=run.attempt,
            )
            log.info(
                "job %s cancelled by %s: %s",
                job.id,
                run.stage.label,
                message,
                extra={"job_id": job.id, "stage": str(run.stage), "event": "cancelled"},
            )
            return _snapshot(session, job)

    def cancel(self, job_id: int, by: str, *, note: str = "") -> JobSnapshot:
        """Stop a job for good; it publishes nothing more. A running stage is told to stop."""
        actor = _staff(by)
        with self._transaction() as (session, now):
            job = _load(session, job_id)
            _require(
                job,
                "cancel",
                JobStatus.QUEUED,
                JobStatus.RUNNING,
                JobStatus.ON_HOLD,
                JobStatus.FAILED,
            )
            previous = job.status
            if previous is JobStatus.RUNNING:
                runs = session.scalars(
                    select(StageRun).where(
                        StageRun.job_id == job.id, StageRun.status == StageRunStatus.RUNNING
                    )
                ).all()
                for run in runs:
                    _close(run, StageRunStatus.CANCELLED, now, error=f"cancelled by {actor[1]}")
                _drop_lease(job)
            job.status = JobStatus.CANCELLED
            job.next_attempt_at = None
            job.finished_at = now
            job.updated_at = now
            _record(
                session,
                job,
                EventKind.CANCELLED,
                actor,
                now,
                f"cancelled by {actor[1]} (was {previous.label.lower()})",
                previous_status=str(previous),
                note=note,
            )
            return _snapshot(session, job)

    def rerun(self, job_id: int, by: str, stage: Stage | str, *, note: str = "") -> JobSnapshot:
        """Run the job again from ``stage`` (FR-32). Earlier stages are not repeated.

        The stage must be one the job has already reached, so no stage is skipped.
        """
        actor = _staff(by)
        stage = Stage(stage)
        with self._transaction() as (session, now):
            job = _load(session, job_id)
            _require(
                job,
                "re-run",
                JobStatus.QUEUED,
                JobStatus.ON_HOLD,
                JobStatus.FAILED,
                JobStatus.DONE,
                JobStatus.CANCELLED,
            )
            if PIPELINE.index(stage) > PIPELINE.index(job.stage):
                raise InvalidTransition(
                    f"cannot re-run job {job.id} from {stage.label}: it has not got past "
                    f"{job.stage.label} yet, so stages would be skipped"
                )
            previous = (job.status, job.stage)
            job.stage = stage
            job.status = JobStatus.QUEUED
            job.attempts = 0
            job.next_attempt_at = None
            job.finished_at = None
            job.hold_reason = None
            job.hold_message = None
            job.last_error = None
            job.updated_at = now
            _record(
                session,
                job,
                EventKind.RERUN,
                actor,
                now,
                f"re-run from {stage.label} by {actor[1]} "
                f"(was {previous[0].label.lower()} at {previous[1].label})",
                previous_status=str(previous[0]),
                previous_stage=str(previous[1]),
                note=note,
            )
            return _snapshot(session, job)

    def expire_leases_of(self, gone: Callable[[str], bool]) -> list[int]:
        """End now the leases of workers that are known to be gone, e.g. a Reel process on
        this computer that no longer runs (T-901). Their jobs are recovered at the next
        claim, as after an expired lease, instead of only when the lease time runs out."""
        with self._transaction() as (session, now):
            ended = []
            for job in session.scalars(
                select(Job).where(Job.status == JobStatus.RUNNING, Job.lease_expires_at > now)
            ):
                if job.lease_owner and gone(job.lease_owner):
                    job.lease_expires_at = now
                    ended.append(job.id)
            return ended

    def identify(
        self,
        job_id: int,
        by: str,
        *,
        group: str,
        lesson: int | None,
        recording_date: date | None,
        note: str = "",
    ) -> JobSnapshot:
        """Staff set the group, lesson number and date of a recording (D-018, UJ-4).

        The identification flags are removed, and the old and new values are written in
        the event. A job held for its identification is released: the held stage runs
        again. Not while a stage runs, because it may be using the old values.
        """
        actor = _staff(by)
        group = group.strip()
        if not group:
            raise ValueError("a group is required")
        if lesson is not None and (type(lesson) is not int or lesson < 0):
            raise ValueError("the lesson number must be 0 or more")
        with self._transaction() as (session, now):
            job = _load(session, job_id)
            _require(
                job, "identify", JobStatus.QUEUED, JobStatus.ON_HOLD, JobStatus.FAILED,
                JobStatus.DONE,
            )  # fmt: skip
            recording = job.recording
            before = {
                "group": recording.group_name,
                "lesson": recording.lesson_number,
                "date": recording.recording_date.isoformat() if recording.recording_date else None,
            }
            after = {
                "group": group,
                "lesson": lesson,
                "date": recording_date.isoformat() if recording_date else None,
            }
            recording.group_name, recording.lesson_number = group, lesson
            recording.recording_date = recording_date
            recording.identification = {
                **(recording.identification or {}),
                "set_by_staff": True,  # a re-run of the import keeps these values
                "staff": {**after, "by": actor[1], "at": now.isoformat()},
            }
            job.flags = [flag for flag in job.flags if flag not in IDENTIFICATION_FLAGS]
            job.updated_at = now
            lesson_text = "no lesson number" if lesson is None else f"lesson {lesson}"
            _record(
                session, job, EventKind.IDENTIFIED, actor, now,
                f"identified by {actor[1]}: {group}, {lesson_text}, date {after['date'] or '-'}",
                before=before, after=after, note=note,
            )  # fmt: skip
            if job.status is JobStatus.ON_HOLD and job.hold_reason == IDENTIFICATION_HOLD:
                job.hold_reason = job.hold_message = None
                job.attempts = 0
                job.next_attempt_at = None
                job.status = JobStatus.QUEUED
                _record(
                    session, job, EventKind.RELEASED, actor, now,
                    f"released by {actor[1]}: {job.stage.label} runs again",
                    reason=IDENTIFICATION_HOLD, advance=False, note=note,
                )  # fmt: skip
            return _snapshot(session, job)

    def retry_now(self, job_id: int, by: str) -> JobSnapshot:
        """Skip the remaining backoff delay of a job that waits for its next try."""
        actor = _staff(by)
        with self._transaction() as (session, now):
            job = _load(session, job_id)
            _require(job, "retry now", JobStatus.QUEUED)
            if job.next_attempt_at is None or job.next_attempt_at <= now:
                raise InvalidTransition(f"job {job.id} is not waiting for a retry")
            job.next_attempt_at = None
            job.updated_at = now
            _record(session, job, EventKind.RETRY_NOW, actor, now, f"retry now by {actor[1]}")
            return _snapshot(session, job)
