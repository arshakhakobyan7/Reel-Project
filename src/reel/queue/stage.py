"""What a pipeline stage looks like to the worker runtime (T-103, NFR-10).

A stage is a class with a ``name`` (one of the pipeline stages), a ``heavy`` flag and a
``run(ctx)`` method. ``run`` returns normally on success (optionally a ``StageResult``) or
raises:

- ``Hold(reason, message)``: a staff decision is needed (the job goes On hold);
- ``StageFailed(message, retryable=...)``: an expected failure;
- ``Discard(message)``: the job must not go on (e.g. a duplicate); it is cancelled;
- any other exception: an unexpected failure, retried like a retryable one.

Long stages call ``ctx.check()`` (or ``ctx.wait()``) now and then. They raise
``StageCancelled`` when the worker stops, the stage times out or staff cancel the job.
External processes register ``ctx.on_cancel(...)`` to be stopped at that moment.
"""

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from reel.config.loader import LoadedConfig
from reel.db import PIPELINE, Database, Stage
from reel.queue.jobs import Claim

log = logging.getLogger("reel.worker")


class Hold(Exception):
    """Raised by a stage to put the job on hold for a staff decision (FR-21, D-018)."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.message = message


class StageFailed(Exception):
    """Raised by a stage for an expected failure. ``retryable=False`` fails at once.
    ``retry_after``: wait at least this many seconds before the next attempt (e.g. until a
    daily quota resets), even when the backoff is shorter."""

    def __init__(
        self, message: str, *, retryable: bool = True, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class Discard(Exception):
    """Raised by a stage when the job must not go on at all (e.g. the recording is a
    duplicate, FR-4): the job is cancelled with this message."""


class StageCancelled(Exception):
    """Raised by ``ctx.check()`` when the stage must stop; the reason says why."""


@dataclass(frozen=True)
class StageResult:
    """Optional return value of ``run``: metrics for the StageRun and a short message."""

    metrics: dict[str, Any] = field(default_factory=dict)
    message: str = ""


class StageContext:
    """Everything a stage needs for one attempt on one job."""

    def __init__(self, claim: Claim, config: LoadedConfig, db: Database, job_dir: Path) -> None:
        self.claim = claim
        self.config = config  # snapshot taken when the stage started (NFR-9)
        self.db = db
        self.job_dir = job_dir
        self.log = logging.getLogger(f"reel.stage.{claim.stage}")
        self._progress: dict[str, Any] | None = None
        self._cancelled = threading.Event()
        self._reason = ""
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def job_id(self) -> int:
        return self.claim.job_id

    @property
    def recording_id(self) -> int:
        return self.claim.recording_id

    @property
    def stage(self) -> Stage:
        return self.claim.stage

    @property
    def attempt(self) -> int:
        return self.claim.attempt

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def cancel_reason(self) -> str:
        return self._reason

    def report_progress(self, done: float, total: float | None = None, note: str = "") -> None:
        """Remember how far the stage is (e.g. seconds of audio). The worker saves the latest
        value with each heartbeat (``StageRun.metrics["progress"]``) for the dashboard."""
        progress: dict[str, Any] = {"done": round(float(done), 3)}
        if total:
            progress["total"] = round(float(total), 3)
            progress["fraction"] = round(min(max(done / total, 0.0), 1.0), 4)
        if note:
            progress["note"] = note
        with self._lock:
            self._progress = progress

    @property
    def progress(self) -> dict[str, Any] | None:
        with self._lock:
            return None if self._progress is None else dict(self._progress)

    def check(self) -> None:
        """Raise ``StageCancelled`` if the stage must stop."""
        if self._cancelled.is_set():
            raise StageCancelled(self._reason)

    def wait(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; return True early if the stage must stop."""
        return self._cancelled.wait(seconds)

    def on_cancel(self, callback: Callable[[], None]) -> None:
        """Call ``callback`` when the stage must stop (e.g. to kill FFmpeg)."""
        with self._lock:
            if not self._cancelled.is_set():
                self._callbacks.append(callback)
                return
        self._run_callback(callback)

    def request_cancel(self, reason: str) -> None:
        """Used by the worker runtime. The first reason wins."""
        with self._lock:
            if self._cancelled.is_set():
                return
            self._reason = reason
            self._cancelled.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            self._run_callback(callback)

    def _run_callback(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            log.exception("a cancel callback of job %s failed", self.job_id)


class StageHandler(ABC):
    """Base class of the pipeline stages."""

    name: ClassVar[Stage]
    heavy: ClassVar[bool] = False  # CPU-heavy (Whisper, rendering, LLM): limited in parallel
    timeout_seconds: ClassVar[float | None] = None  # None = workers.stage_timeout_minutes

    @abstractmethod
    def run(self, ctx: StageContext) -> StageResult | None:
        """Do the stage's work for ``ctx.job_id``. Must be safe to run again (NFR-2)."""


class StageRegistry:
    """The stage implementations a worker can run."""

    def __init__(self, handlers: Iterable[StageHandler] = ()) -> None:
        self._handlers: dict[Stage, StageHandler] = {}
        for handler in handlers:
            self.register(handler)

    def register(self, handler: StageHandler) -> None:
        stage = Stage(handler.name)
        if stage in self._handlers:
            raise ValueError(f"stage {stage} is registered twice")
        self._handlers[stage] = handler

    def get(self, stage: Stage) -> StageHandler | None:
        return self._handlers.get(Stage(stage))

    def stages(self) -> tuple[Stage, ...]:
        """Registered stages in pipeline order."""
        return tuple(stage for stage in PIPELINE if stage in self._handlers)

    def heavy(self) -> tuple[Stage, ...]:
        return tuple(stage for stage in self.stages() if self._handlers[stage].heavy)

    def missing(self) -> tuple[Stage, ...]:
        """Pipeline stages without an implementation; jobs wait at these stages."""
        return tuple(stage for stage in PIPELINE if stage not in self._handlers)
