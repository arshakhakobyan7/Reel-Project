"""Worker runtime (T-103) with dummy stages: order, retries, holds, the heavy-stage limit,
timeouts, staff cancel, graceful stop and the ``reel worker`` command."""

import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from reel import power
from reel.cli import main
from reel.config import load_config
from reel.db import (
    PIPELINE,
    JobEvent,
    JobStatus,
    Recording,
    Stage,
    StageRun,
    StageRunStatus,
    init_database,
    open_database,
)
from reel.logging_setup import current_context
from reel.queue import (
    Attention,
    Hold,
    JobQueue,
    StageFailed,
    StageHandler,
    StageRegistry,
    StageResult,
    Worker,
)

HEAVY = {Stage.TRANSCRIBE, Stage.CLEAN, Stage.CHAPTERS}
FAST = """
[workers]
count = 3
heavy_stage_limit = {limit}
max_attempts = {attempts}
backoff_base_seconds = 0.01
backoff_factor = 1.0
idle_poll_seconds = 0.02
stop_grace_seconds = {grace}
"""


class Dummy(StageHandler):
    """A stage whose behaviour the test chooses; records every call."""

    def __init__(self, stage, log, *, heavy=False, timeout=None, action=None):
        self.name = stage
        self.heavy = heavy
        self.timeout_seconds = timeout
        self.log = log
        self.action = action

    def run(self, ctx):
        self.log.enter(self, ctx)
        try:
            return self.action(ctx) if self.action else None
        finally:
            self.log.leave(self)


class CallLog:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[tuple[int, Stage]] = []
        self.contexts: list[dict] = []
        self.heavy_now = 0
        self.heavy_max = 0

    def enter(self, stage, ctx) -> None:
        with self.lock:
            self.calls.append((ctx.job_id, ctx.stage))
            self.contexts.append(current_context())
            if stage.heavy:
                self.heavy_now += 1
                self.heavy_max = max(self.heavy_max, self.heavy_now)

    def leave(self, stage) -> None:
        if stage.heavy:
            with self.lock:
                self.heavy_now -= 1


@pytest.fixture
def setup(project, write_config):
    """Returns make(limit, attempts, grace) -> (cfg, db, queue)."""
    opened = []

    def make(limit=1, attempts=3, grace=2.0):
        write_config(FAST.format(limit=limit, attempts=attempts, grace=grace))
        cfg = load_config(project / "config" / "reel.toml")
        init_database(cfg.paths.database_file)
        db = open_database(cfg.paths.database_file)
        opened.append(db)
        return cfg, db, JobQueue.from_settings(db, cfg.settings.workers)

    yield make
    for db in opened:
        db.dispose()


@pytest.fixture
def workers():
    """Start workers through this fixture so they are always stopped."""
    started: list[Worker] = []

    def start(worker: Worker, *, until_idle: bool = False) -> Worker:
        started.append(worker)
        worker.start(until_idle=until_idle)
        return worker

    yield start
    for worker in started:
        worker.stop(timeout=10)


def registry(log: CallLog, **actions) -> StageRegistry:
    """Dummy stages for the whole pipeline; ``actions`` override stages by name."""
    return StageRegistry(
        Dummy(stage, log, heavy=stage in HEAVY, action=actions.get(stage.value))
        for stage in PIPELINE
    )


def add_jobs(db, queue: JobQueue, count: int, stage: Stage = Stage.IMPORT) -> list[int]:
    ids = []
    for _ in range(count):
        with db.transaction() as s:
            recording = Recording(source_path="inbox/x.mp4", size_bytes=1)
            s.add(recording)
            s.flush()
            recording_id = recording.id
        ids.append(queue.enqueue(recording_id, stage=stage).id)
    return ids


def wait_for(condition, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.01)


def stage_runs(db, job_id: int) -> list[tuple[Stage, StageRunStatus]]:
    with db.transaction() as s:
        query = select(StageRun).where(StageRun.job_id == job_id).order_by(StageRun.id)
        return [(run.stage, run.status) for run in s.scalars(query)]


def until_cancelled(ctx):
    """A well-behaved long stage: works until told to stop."""
    while not ctx.wait(0.01):
        pass
    ctx.check()


# --- normal operation ------------------------------------------------------------------------


def test_a_job_passes_all_stages_in_order(setup, workers):
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1)
    log = CallLog()
    worker = workers(Worker(db, registry(log), lambda: cfg), until_idle=True)
    assert worker.join(timeout=20)
    assert [stage for _, stage in log.calls] == list(PIPELINE)
    assert queue.get(job_id).status is JobStatus.DONE
    assert (cfg.paths.data_dir / "jobs" / str(job_id)).is_dir()
    context = log.contexts[0]
    assert (context["job_id"], context["stage"], context["attempt"]) == (job_id, "import", 1)
    assert context["worker"] in worker.names


def test_stage_result_metrics_and_message_are_saved(setup, workers):
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1, Stage.CHAPTERS)

    def chapters(ctx):
        assert ctx.config is cfg and ctx.db is db
        return StageResult(metrics={"llm_tokens": 1234}, message="5 chapters")

    workers(Worker(db, registry(CallLog(), chapters=chapters), lambda: cfg), until_idle=True)
    wait_for(lambda: queue.get(job_id).status is JobStatus.DONE)
    with db.transaction() as s:
        run = s.scalars(select(StageRun).where(StageRun.stage == Stage.CHAPTERS)).one()
        assert run.metrics == {"llm_tokens": 1234}
        messages = s.scalars(select(JobEvent.message).where(JobEvent.job_id == job_id)).all()
    assert any(message.endswith("5 chapters") for message in messages)


def test_a_failing_stage_is_retried_then_fails_and_staff_are_notified(setup, workers):
    cfg, db, queue = setup(attempts=3)
    (job_id,) = add_jobs(db, queue, 1, Stage.CLEAN)
    notices: list[Attention] = []

    def clean(ctx):
        raise StageFailed("renderer crashed")

    worker = Worker(db, registry(CallLog(), clean=clean), lambda: cfg, notifier=notices.append)
    workers(worker, until_idle=True)  # waits for the scheduled retries, then stops
    assert worker.join(timeout=20)
    job = queue.get(job_id)
    assert (job.status, job.stage, job.attempts) == (JobStatus.FAILED, Stage.CLEAN, 3)
    assert stage_runs(db, job_id) == [(Stage.CLEAN, StageRunStatus.FAILED)] * 3
    (notice,) = notices
    assert (notice.kind, notice.stage, notice.job.id) == ("failed", Stage.CLEAN, job_id)
    assert notice.message == "renderer crashed"


def test_a_fatal_failure_is_not_retried(setup, workers):
    cfg, db, queue = setup(attempts=3)
    (job_id,) = add_jobs(db, queue, 1)

    def import_(ctx):
        raise StageFailed("not a video file", retryable=False)

    workers(Worker(db, registry(CallLog(), **{"import": import_}), lambda: cfg), until_idle=True)
    wait_for(lambda: queue.get(job_id).status is JobStatus.FAILED)
    assert stage_runs(db, job_id) == [(Stage.IMPORT, StageRunStatus.FAILED)]


def test_an_unexpected_error_is_retried(setup, workers):
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1, Stage.POST)
    calls = []

    def post(ctx):
        calls.append(ctx.attempt)
        if len(calls) == 1:
            raise ValueError("boom")

    workers(Worker(db, registry(CallLog(), post=post), lambda: cfg), until_idle=True)
    wait_for(lambda: queue.get(job_id).status is JobStatus.DONE)
    assert calls == [1, 2]
    with db.transaction() as s:
        errors = s.scalars(select(StageRun.error).where(StageRun.job_id == job_id)).all()
    assert errors == ["ValueError: boom", None]


def test_a_stage_that_raises_hold_puts_the_job_on_hold(setup, workers):
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1, Stage.CONSENT)
    notices: list[Attention] = []
    log = CallLog()

    def consent(ctx):
        raise Hold("consent_opted_out", "1 student opted out")

    worker = Worker(db, registry(log, consent=consent), lambda: cfg, notifier=notices.append)
    workers(worker, until_idle=True)  # a held job is not waited for
    assert worker.join(timeout=20)
    job = queue.get(job_id)
    assert (job.status, job.hold_reason) == (JobStatus.ON_HOLD, "consent_opted_out")
    assert [stage for _, stage in log.calls] == [Stage.CONSENT]  # nothing after it ran
    (notice,) = notices
    assert (notice.kind, notice.message) == ("held", "1 student opted out")


def test_an_invalid_hold_reason_fails_the_job(setup, workers):
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1, Stage.CONSENT)

    def consent(ctx):
        raise Hold("Not a code")

    workers(Worker(db, registry(CallLog(), consent=consent), lambda: cfg), until_idle=True)
    wait_for(lambda: queue.get(job_id).status is JobStatus.FAILED)
    assert "invalid hold" in queue.get(job_id).last_error


def test_a_failing_notifier_does_not_stop_the_worker(setup, workers):
    cfg, db, queue = setup(attempts=1)
    failing, fine = add_jobs(db, queue, 2, Stage.POST)

    def post(ctx):
        if ctx.job_id == failing:
            raise StageFailed("slack down")

    def broken_notifier(_attention):
        raise RuntimeError("notifier bug")

    worker = Worker(db, registry(CallLog(), post=post), lambda: cfg, notifier=broken_notifier)
    workers(worker, until_idle=True)
    assert worker.join(timeout=20)
    assert queue.get(failing).status is JobStatus.FAILED
    assert queue.get(fine).status is JobStatus.DONE


# --- the heavy-stage limit -------------------------------------------------------------------


@pytest.mark.parametrize(("limit", "expected"), [(1, 1), (3, 3)])
def test_heavy_stage_limit(setup, workers, limit, expected):
    """3 jobs, 3 slots: limit 1 → never two heavy stages at once; limit 3 → they overlap
    (which shows the measurement can see parallel stages)."""
    cfg, db, queue = setup(limit=limit)
    job_ids = add_jobs(db, queue, 3, Stage.TRANSCRIBE)
    log = CallLog()

    def heavy_work(ctx):
        ctx.wait(0.15)

    actions = {"transcribe": heavy_work, "clean": heavy_work, "chapters": heavy_work}
    worker = workers(Worker(db, registry(log, **actions), lambda: cfg), until_idle=True)
    assert worker.join(timeout=30)
    assert {queue.get(job_id).status for job_id in job_ids} == {JobStatus.DONE}
    assert log.heavy_max == expected


# --- stopping, timeouts, cancel --------------------------------------------------------------


def test_stopping_the_worker_gives_running_jobs_back(setup, workers):
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1, Stage.TRANSCRIBE)
    seen = {}

    def transcribe(ctx):
        seen["ctx"] = ctx
        until_cancelled(ctx)

    worker = workers(Worker(db, registry(CallLog(), transcribe=transcribe), lambda: cfg))
    wait_for(lambda: queue.get(job_id).status is JobStatus.RUNNING and "ctx" in seen)
    assert worker.stop(timeout=10)
    job = queue.get(job_id)
    assert (job.status, job.lease_owner, job.attempts) == (JobStatus.QUEUED, None, 0)
    assert stage_runs(db, job_id) == [(Stage.TRANSCRIBE, StageRunStatus.INTERRUPTED)]
    assert seen["ctx"].cancel_reason == "the worker is stopping"


def test_a_stage_that_ignores_the_stop_is_abandoned_after_the_grace_time(setup, workers):
    cfg, db, queue = setup(grace=0.2)
    (job_id,) = add_jobs(db, queue, 1, Stage.TRANSCRIBE)
    release = threading.Event()
    returned = threading.Event()

    def transcribe(ctx):
        release.wait(10)  # ignores ctx: e.g. stuck in a library call
        returned.set()

    worker = workers(Worker(db, registry(CallLog(), transcribe=transcribe), lambda: cfg))
    wait_for(lambda: queue.get(job_id).status is JobStatus.RUNNING)
    started = time.monotonic()
    assert worker.stop(timeout=10)
    assert time.monotonic() - started < 5
    assert queue.get(job_id).status is JobStatus.QUEUED  # the lease was given back
    before = queue.get(job_id)
    release.set()
    assert returned.wait(5)
    time.sleep(0.1)
    assert queue.get(job_id) == before  # the late result changed nothing


def test_a_stage_that_runs_too_long_is_stopped_and_counted_as_a_failure(setup, workers):
    cfg, db, queue = setup(attempts=1)
    (job_id,) = add_jobs(db, queue, 1, Stage.TRANSCRIBE)
    notices: list[Attention] = []
    log = CallLog()
    handlers = registry(log)
    slow = Dummy(Stage.TRANSCRIBE, log, heavy=True, timeout=0.2, action=until_cancelled)
    handlers._handlers[Stage.TRANSCRIBE] = slow
    workers(Worker(db, handlers, lambda: cfg, notifier=notices.append), until_idle=True)
    wait_for(lambda: queue.get(job_id).status is JobStatus.FAILED)
    assert queue.get(job_id).last_error == "Transcribed timed out after 0.2 s"
    assert [notice.kind for notice in notices] == ["failed"]


def test_time_the_pc_slept_is_not_counted_as_stage_time(setup, workers, monkeypatch):
    """T-1002: a laptop slept for ten hours during a stage, and the stage was stopped as
    timed out when the PC woke. Only awake time counts now (D-056)."""
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1, Stage.TRANSCRIBE)
    monkeypatch.setattr(power, "awake_clock", lambda: 1000.0)  # the PC "sleeps" throughout
    log = CallLog()
    handlers = registry(log)

    def long_stage(ctx):
        ctx.wait(1.3)

    sleepy = Dummy(Stage.TRANSCRIBE, log, heavy=True, timeout=0.3, action=long_stage)
    handlers._handlers[Stage.TRANSCRIBE] = sleepy
    workers(Worker(db, handlers, lambda: cfg, heartbeat_seconds=0.05), until_idle=True)
    wait_for(lambda: queue.get(job_id).status is JobStatus.DONE)
    with db.transaction() as session:
        run = session.scalars(
            select(StageRun).where(StageRun.job_id == job_id, StageRun.stage == Stage.TRANSCRIBE)
        ).one()
        assert run.status is StageRunStatus.SUCCEEDED
        assert 1.2 <= run.metrics["suspended_seconds"] <= 5


def test_a_stage_keeps_the_pc_awake_while_it_runs(setup, workers, monkeypatch):
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1, Stage.TRANSCRIBE)
    flags: list[tuple[str, int]] = []
    monkeypatch.setattr(
        power,
        "_set_execution_state",
        lambda value: flags.append((threading.current_thread().name, value)) or True,
    )
    workers(Worker(db, registry(CallLog()), lambda: cfg), until_idle=True)
    wait_for(lambda: queue.get(job_id).status is JobStatus.DONE)
    stages = len(PIPELINE) - PIPELINE.index(Stage.TRANSCRIBE)
    want = [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS] * stages
    assert [value for _, value in flags] == want
    assert len({name for name, _ in flags[:2]}) == 1  # set and cleared in the stage thread


def test_staff_cancel_stops_the_running_stage(setup, workers):
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1, Stage.TRANSCRIBE)
    seen = {}

    def transcribe(ctx):
        seen["ctx"] = ctx
        until_cancelled(ctx)

    worker = Worker(
        db, registry(CallLog(), transcribe=transcribe), lambda: cfg, heartbeat_seconds=0.05
    )
    workers(worker)
    wait_for(lambda: "ctx" in seen)
    queue.cancel(job_id, "anna", note="wrong recording")
    wait_for(lambda: seen["ctx"].cancelled and not worker.running())
    assert "cancelled by staff" in seen["ctx"].cancel_reason
    job = queue.get(job_id)
    assert job.status is JobStatus.CANCELLED
    assert stage_runs(db, job_id) == [(Stage.TRANSCRIBE, StageRunStatus.CANCELLED)]


def test_heartbeats_renew_the_lease_during_a_long_stage(setup, workers):
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1, Stage.TRANSCRIBE)
    leases = []

    def transcribe(ctx):
        for _ in range(3):
            leases.append(queue.get(job_id).lease_expires_at)
            ctx.wait(0.15)

    worker = Worker(
        db, registry(CallLog(), transcribe=transcribe), lambda: cfg, heartbeat_seconds=0.05
    )
    workers(worker, until_idle=True)
    wait_for(lambda: queue.get(job_id).status is JobStatus.DONE)
    assert leases[0] < leases[1] < leases[2]


def test_cancel_callbacks_run_when_the_stage_must_stop(setup, workers):
    cfg, db, queue = setup()
    add_jobs(db, queue, 1, Stage.TRANSCRIBE)
    called = threading.Event()
    registered = threading.Event()

    def transcribe(ctx):
        ctx.on_cancel(called.set)  # e.g. kill an FFmpeg process
        registered.set()
        until_cancelled(ctx)

    worker = workers(Worker(db, registry(CallLog(), transcribe=transcribe), lambda: cfg))
    assert registered.wait(10)
    worker.stop(timeout=10)
    assert called.is_set()


def test_a_worker_without_stage_implementations_claims_nothing(setup, workers):
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1)
    worker = workers(Worker(db, StageRegistry(), lambda: cfg), until_idle=True)
    assert worker.join(timeout=5)
    assert queue.get(job_id).status is JobStatus.QUEUED


def test_start_twice_is_an_error(setup, workers):
    cfg, db, _ = setup()
    worker = workers(Worker(db, StageRegistry(), lambda: cfg), until_idle=True)
    with pytest.raises(RuntimeError):
        worker.start()


# --- registry --------------------------------------------------------------------------------


def test_registry():
    log = CallLog()
    handlers = StageRegistry(
        [Dummy(Stage.CLEAN, log, heavy=True), Dummy(Stage.IMPORT, log), Dummy(Stage.POST, log)]
    )
    assert handlers.stages() == (Stage.IMPORT, Stage.CLEAN, Stage.POST)  # pipeline order
    assert handlers.heavy() == (Stage.CLEAN,)
    assert handlers.missing() == (Stage.TRANSCRIBE, Stage.CHAPTERS, Stage.CONSENT, Stage.UPLOAD)
    assert handlers.get(Stage.CLEAN).heavy
    assert handlers.get(Stage.UPLOAD) is None
    with pytest.raises(ValueError, match="registered twice"):
        handlers.register(Dummy(Stage.CLEAN, log))


# --- reel worker -----------------------------------------------------------------------------


def worker_args(project: Path, *extra: str) -> list[str]:
    return ["--config", str(project / "config" / "reel.toml"), "worker", *extra]


def test_reel_worker_needs_an_initialised_database(project, capsys):
    assert main(worker_args(project, "--until-idle")) == 1
    assert "reel db init" in capsys.readouterr().err


def test_reel_worker_runs_every_pipeline_stage(setup, project, capsys):
    cfg, db, queue = setup()
    (job_id,) = add_jobs(db, queue, 1)
    assert main(worker_args(project, "--until-idle")) == 0
    err = capsys.readouterr().err
    assert "worker started: 3 slot(s), heavy-stage limit 1" in err
    assert "no implementation yet" not in err  # all stages exist since T-706
    assert "worker stopped" in err
    # The real import stage (T-204) ran: the test job has no file, so it failed.
    assert "the recording no longer exists" in err
    assert queue.get(job_id).status is JobStatus.FAILED
    assert (project / "logs" / "reel-worker.jsonl").is_file()


def test_reel_worker_runs_registered_stages(setup, project, monkeypatch):
    cfg, db, queue = setup()
    job_ids = add_jobs(db, queue, 2)
    log = CallLog()
    monkeypatch.setattr("reel.stages.default_registry", lambda: registry(log))
    assert main(worker_args(project, "--until-idle")) == 0
    assert {queue.get(job_id).status for job_id in job_ids} == {JobStatus.DONE}
    assert len(log.calls) == 2 * len(PIPELINE)
    job_log = project / "data" / "jobs" / str(job_ids[0]) / "job.log"
    assert "Posting links finished; all stages done" in job_log.read_text(encoding="utf-8")


def test_ctrl_c_asks_the_worker_to_stop_and_the_old_handler_comes_back():
    import signal

    from reel.queue.commands import stop_on_signals

    original = signal.getsignal(signal.SIGINT)
    stops = []
    with stop_on_signals(lambda: stops.append(True)):
        signal.raise_signal(signal.SIGINT)  # what Ctrl+C sends; no KeyboardInterrupt now
    assert stops == [True]
    assert signal.getsignal(signal.SIGINT) is original
