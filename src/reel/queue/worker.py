"""Worker runtime: runs pipeline stages from the job queue (T-103; NFR-3, NFR-10, FR-31).

A worker has ``workers.count`` slots (threads). A slot claims a job, runs the job's current
stage in a separate stage thread and, while it runs:

- renews the lease (heartbeat) every third of ``workers.lease_seconds``;
- stops the stage when it runs longer than its timeout (a retryable failure); the timeout
  counts only the time the PC was awake (D-056);
- keeps Windows from sleeping for being idle while the stage runs (D-056);
- stops the stage when staff cancelled the job (the heartbeat reports ``LeaseLost``);
- stops the stage when the worker stops, and gives the job back without using an attempt.

A stage that does not stop within ``workers.stop_grace_seconds`` is abandoned (Python cannot
kill a thread). Its job is handled as above, and whatever the stage returns later is
ignored. At most ``workers.heavy_stage_limit`` heavy stages run at the same time, across all
workers, because the limit is checked in the claim transaction.
"""

import logging
import math
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from reel import power
from reel.config.loader import LoadedConfig
from reel.db import Database, JobStatus, Stage
from reel.logging_setup import log_context
from reel.metrics import check_budget
from reel.queue.jobs import Claim, JobQueue, JobSnapshot, LeaseLost
from reel.queue.stage import (
    Discard,
    Hold,
    StageCancelled,
    StageContext,
    StageFailed,
    StageHandler,
    StageRegistry,
    StageResult,
)

log = logging.getLogger("reel.worker")

STOPPING = "the worker is stopping"
LOST = "the job was cancelled by staff or given to another worker"
SLEEP_NOTED_SECONDS = 1.0  # shorter differences between the two clocks are noise


@dataclass(frozen=True)
class Attention:
    """A job needs staff attention (FR-33): it failed or went on hold."""

    kind: Literal["failed", "held"]
    job: JobSnapshot
    stage: Stage
    message: str


Notifier = Callable[[Attention], None]


def log_notifier(attention: Attention) -> None:
    """Default notifier: a warning in the logs. Slack alerts replace it in T-801."""
    log.warning(
        "job %s %s at %s: %s",
        attention.job.id,
        "failed" if attention.kind == "failed" else "is on hold",
        attention.stage.label,
        attention.message,
        extra={"job_id": attention.job.id, "stage": str(attention.stage)},
    )


@dataclass
class _Outcome:
    done: threading.Event = field(default_factory=threading.Event)
    result: StageResult | None = None
    error: BaseException | None = None


class Worker:
    """Runs stages from the queue in ``slots`` threads until stopped."""

    def __init__(
        self,
        db: Database,
        registry: StageRegistry,
        config: Callable[[], LoadedConfig],
        *,
        notifier: Notifier = log_notifier,
        slots: int | None = None,
        heartbeat_seconds: float | None = None,
        name_prefix: str | None = None,
    ) -> None:
        self.db = db
        self.registry = registry
        self.notifier = notifier
        self._config = config
        self._heartbeat_seconds = heartbeat_seconds
        workers = config().settings.workers
        self.queue = JobQueue.from_settings(db, workers)
        prefix = name_prefix or f"{socket.gethostname()}-{os.getpid()}"
        self.names = [f"{prefix}-{n}" for n in range(1, (slots or workers.count) + 1)]
        self._stopping = threading.Event()
        self._until_idle = False
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._running: dict[str, Claim] = {}

    # --- control -------------------------------------------------------------------------

    def start(self, *, until_idle: bool = False) -> None:
        """Start the slots. With ``until_idle`` they stop when no queued or running job is
        left at a stage this worker can run (jobs waiting for a retry are waited for)."""
        if self._threads:
            raise RuntimeError("the worker was already started")
        self._until_idle = until_idle
        for name in self.names:
            thread = threading.Thread(target=self._slot, args=(name,), name=name, daemon=True)
            self._threads.append(thread)
            thread.start()

    def request_stop(self) -> None:
        """Ask the slots to stop (safe to call from a signal handler)."""
        self._stopping.set()

    def stop(self, timeout: float | None = None) -> bool:
        """Stop and wait for the slots; running jobs are given back. True if all stopped."""
        self._stopping.set()
        return self.join(timeout)

    def join(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            thread.join(remaining)
        return not self.alive

    @property
    def alive(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)

    def running(self) -> dict[str, Claim]:
        """The claims being worked on now, by slot name."""
        with self._lock:
            return dict(self._running)

    # --- slot loop -----------------------------------------------------------------------

    def _slot(self, name: str) -> None:
        poll = 5.0
        while not self._stopping.is_set():
            try:
                cfg = self._config()
                workers = cfg.settings.workers
                poll = workers.idle_poll_seconds
                self.queue.apply_settings(workers)
                claim = self.queue.claim(
                    name,
                    stages=self.registry.stages(),
                    heavy_stages=self.registry.heavy(),
                    heavy_limit=workers.heavy_stage_limit,
                    keep=tuple(self.running()),  # alive here, even after the PC slept
                )
                if claim is None:
                    if self._until_idle and not self.queue.active_count(self.registry.stages()):
                        return
                    self._stopping.wait(poll)
                    continue
                self._run(claim, cfg)
            except Exception:
                log.exception("worker %s: unexpected problem; trying again", name)
                self._stopping.wait(poll)

    def _run(self, claim: Claim, cfg: LoadedConfig) -> None:
        handler = self.registry.get(claim.stage)
        if handler is None:  # claims are limited to registered stages; kept for safety
            self._safely(self.queue.interrupt, claim, "no implementation for this stage")
            return
        workers = cfg.settings.workers
        job_dir = cfg.paths.data_dir / "jobs" / str(claim.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        ctx = StageContext(claim, cfg, self.db, job_dir)
        outcome = _Outcome()
        timeout = handler.timeout_seconds or workers.stage_timeout_minutes * 60
        beat_every = self._heartbeat_seconds or self.queue.lease_seconds / 3
        thread = threading.Thread(
            target=self._stage_thread,
            args=(handler, ctx, outcome),
            name=f"{claim.worker}-{claim.stage}",
            daemon=True,
        )
        # Heartbeats follow the monotonic clock, which on Windows goes on during sleep, so the
        # lease is renewed as soon as the PC wakes. The timeout follows the awake clock.
        started, awake_started = time.monotonic(), power.awake_clock()
        deadline, next_beat = awake_started + timeout, started + beat_every
        grace_until: float | None = None
        why: str | None = None  # "stopping", "timeout" or "lost"
        with self._lock:
            self._running[claim.worker] = claim
        thread.start()
        try:
            while not outcome.done.is_set():
                now, awake = time.monotonic(), power.awake_clock()
                if why is None and self._stopping.is_set():
                    why = "stopping"
                    ctx.request_cancel(STOPPING)
                elif why is None and awake >= deadline:
                    why = "timeout"
                    ctx.request_cancel(f"timed out after {timeout:g} s")
                if now >= next_beat and why != "lost":
                    next_beat = now + beat_every
                    try:
                        self.queue.heartbeat(claim, progress=ctx.progress)
                    except LeaseLost:
                        why = "lost"
                        ctx.request_cancel(LOST)
                    except Exception:
                        log.warning("heartbeat for job %s failed; trying again", claim.job_id)
                if why is not None and grace_until is None:
                    grace_until = now + workers.stop_grace_seconds
                if grace_until is not None and now >= grace_until:
                    break
                wake = min(next_beat, now + deadline - awake if why is None else math.inf)
                wake = min(wake, grace_until if grace_until is not None else math.inf)
                outcome.done.wait(max(0.005, min(wake - now, 0.25)))
        finally:
            with self._lock:
                self._running.pop(claim.worker, None)
        asleep = (time.monotonic() - started) - (power.awake_clock() - awake_started)
        extra: dict[str, Any] = {}
        if asleep >= SLEEP_NOTED_SECONDS:
            extra["suspended_seconds"] = round(asleep, 1)
            log.info(
                "job %s: the PC was asleep for %.0f min during %s; not counted as work",
                claim.job_id, asleep / 60, claim.stage.label,
                extra={"job_id": claim.job_id, "stage": str(claim.stage)},
            )  # fmt: skip
        self._finish(claim, outcome, why, timeout, extra)
        if why != "lost":
            self._check_budget(claim, cfg)

    def _check_budget(self, claim: Claim, cfg: LoadedConfig) -> None:
        """Flag the job once when its processing time passes the budget (T-805, NFR-8)."""
        limit = cfg.settings.budget.max_processing_minutes
        try:
            with self.db.transaction(immediate=True) as session:
                minutes = check_budget(session, claim.job_id, limit)
        except Exception:
            log.exception("the budget check of job %s failed", claim.job_id)
            return
        if minutes is not None:
            log.warning(
                "job %s: processing has taken %.0f min, more than the budget of %g min",
                claim.job_id, minutes, limit,
            )  # fmt: skip

    def _stage_thread(self, handler: StageHandler, ctx: StageContext, outcome: _Outcome) -> None:
        claim = ctx.claim
        context = {
            "job_id": claim.job_id,
            "recording_id": claim.recording_id,
            "stage": str(claim.stage),
            "attempt": claim.attempt,
            "worker": claim.worker,
        }
        with log_context(**context):
            try:
                with power.keep_awake(ctx.config.settings.workers.keep_awake):
                    result = handler.run(ctx)
                if result is not None and not isinstance(result, StageResult):
                    raise TypeError(f"run() returned {type(result).__name__}, not StageResult")
                outcome.result = result
            except BaseException as exc:  # recorded for the slot, which decides what to do
                if not isinstance(exc, Hold | StageFailed | StageCancelled):
                    log.exception("%s raised an unexpected error", claim.stage.label)
                outcome.error = exc
            finally:
                outcome.done.set()

    # --- results -------------------------------------------------------------------------

    def _finish(
        self,
        claim: Claim,
        outcome: _Outcome,
        why: str | None,
        timeout: float,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Record the outcome. ``extra`` metrics (the time asleep) go on the stage run."""
        extra = extra or None
        finished, error = outcome.done.is_set(), outcome.error
        if not finished:
            log.warning(
                "job %s: %s did not stop in time and was abandoned",
                claim.job_id,
                claim.stage.label,
                extra={"job_id": claim.job_id, "stage": str(claim.stage)},
            )
        if why == "lost":
            log.info(
                "job %s: the result of %s is discarded (%s)",
                claim.job_id,
                claim.stage.label,
                LOST,
                extra={"job_id": claim.job_id, "stage": str(claim.stage)},
            )
            return
        if finished and error is None:
            result = outcome.result or StageResult()
            metrics = {**result.metrics, **extra} if extra else result.metrics
            self._safely(self.queue.complete, claim, metrics=metrics, message=result.message)
            return
        stopped = not finished or isinstance(error, StageCancelled)
        if why == "stopping" and stopped:
            self._safely(self.queue.interrupt, claim, STOPPING)
        elif why == "timeout" and stopped:
            message = f"{claim.stage.label} timed out after {timeout:g} s"
            self._fail(claim, message, True, metrics=extra)
        elif isinstance(error, Hold):
            self._hold(claim, error, extra)
        elif isinstance(error, Discard):
            self._safely(self.queue.discard, claim, str(error))
        elif isinstance(error, StageFailed):
            self._fail(claim, str(error), error.retryable, error.retry_after, metrics=extra)
        else:
            self._fail(claim, f"{type(error).__name__}: {error}", True, metrics=extra)

    def _hold(self, claim: Claim, hold: Hold, extra: dict[str, Any] | None = None) -> None:
        try:
            job = self.queue.hold(claim, hold.reason, hold.message, metrics=extra)
        except ValueError as exc:  # a stage bug: an invalid reason code
            self._fail(claim, f"invalid hold from the stage: {exc}", False)
            return
        except LeaseLost as exc:
            log.info("%s; result discarded", exc)
            return
        except Exception:
            log.exception("could not put job %s on hold", claim.job_id)
            return
        self._notify("held", job, claim.stage, hold.message or hold.reason)

    def _fail(
        self,
        claim: Claim,
        message: str,
        retryable: bool,
        retry_after: float | None = None,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        job = self._safely(
            self.queue.fail,
            claim,
            message,
            retryable=retryable,
            retry_after=retry_after,
            metrics=metrics,
        )
        if job is not None and job.status is JobStatus.FAILED:
            self._notify("failed", job, claim.stage, message)

    def _safely(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a queue operation for a claim. If the claim is no longer valid, or the
        database fails, log it: an unfinished claim's lease expires and the job is
        recovered, so nothing is lost."""
        try:
            return operation(*args, **kwargs)
        except LeaseLost as exc:
            log.info("%s; result discarded", exc)
        except Exception:
            log.exception("queue operation %s failed", getattr(operation, "__name__", operation))
        return None

    def _notify(
        self, kind: Literal["failed", "held"], job: JobSnapshot, stage: Stage, text: str
    ) -> None:
        try:
            self.notifier(Attention(kind, job, stage, text))
        except Exception:
            log.exception("the notifier failed for job %s", job.id)
