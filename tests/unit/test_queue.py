"""Job queue and state machine (T-102): every transition, invalid transitions, backoff,
crash recovery, and concurrent claimers (threads and separate processes)."""

import json
import logging
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from reel.config.schema import Settings
from reel.db import (
    PIPELINE,
    ActorKind,
    EventKind,
    Job,
    JobEvent,
    JobStatus,
    Recording,
    Stage,
    StageRun,
    StageRunStatus,
)
from reel.queue import (
    Claim,
    InvalidTransition,
    JobNotFound,
    JobQueue,
    LeaseLost,
    QueueError,
    RetryPolicy,
    create_job,
)

T0 = datetime(2026, 11, 18, 14, 0, tzinfo=UTC)
HEAVY = (Stage.TRANSCRIBE, Stage.CLEAN, Stage.CHAPTERS)


class Clock:
    """A fake clock the tests move forward by hand."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def queue(db, clock) -> JobQueue:
    policy = RetryPolicy(
        max_attempts=3, backoff_base_seconds=60, backoff_factor=4, backoff_max_seconds=3600
    )
    return JobQueue(db, policy=policy, lease_seconds=300, clock=clock)


def new_recording(db) -> int:
    with db.transaction() as s:
        recording = Recording(source_path="inbox/lesson.mp4", size_bytes=1)
        s.add(recording)
        s.flush()
        return recording.id


def new_job(queue: JobQueue, stage: Stage = Stage.IMPORT) -> int:
    return queue.enqueue(new_recording(queue.db), stage=stage).id


def kinds(db, job_id: int) -> list[str]:
    with db.transaction() as s:
        query = select(JobEvent.kind).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
        return list(s.scalars(query))


def last_event(db, job_id: int) -> JobEvent:
    with db.transaction() as s:
        query = select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id.desc())
        return s.scalars(query.limit(1)).one()


def runs(db, job_id: int) -> list[tuple[Stage, int, StageRunStatus]]:
    with db.transaction() as s:
        query = select(StageRun).where(StageRun.job_id == job_id).order_by(StageRun.id)
        return [(r.stage, r.attempt, r.status) for r in s.scalars(query)]


def run_to_done(queue: JobQueue, worker: str = "w1") -> list[Stage]:
    stages = []
    while (claim := queue.claim(worker)) is not None:
        stages.append(claim.stage)
        queue.complete(claim)
    return stages


# --- creating jobs ---------------------------------------------------------------------------


def test_enqueue_creates_a_queued_job(queue, db):
    job = queue.enqueue(new_recording(db))
    assert (job.stage, job.status, job.attempts) == (Stage.IMPORT, JobStatus.QUEUED, 0)
    event = last_event(db, job.id)
    assert event.kind == EventKind.CREATED
    assert event.actor_kind is ActorKind.SYSTEM
    assert event.at == T0


def test_enqueue_rejects_a_second_job_or_an_unknown_recording(queue, db):
    recording_id = new_recording(db)
    queue.enqueue(recording_id)
    with pytest.raises(InvalidTransition, match="already has job"):
        queue.enqueue(recording_id)
    with pytest.raises(QueueError, match="does not exist"):
        queue.enqueue(999)


def test_create_job_joins_the_callers_transaction(db):
    with db.transaction() as s:
        recording = Recording(source_path="inbox/a.mp4", size_bytes=1)
        s.add(recording)
        job = create_job(s, recording, by="anna")
        assert job.id is not None
    with pytest.raises(RuntimeError), db.transaction() as s:
        recording = Recording(source_path="inbox/b.mp4", size_bytes=1)
        s.add(recording)
        create_job(s, recording)
        raise RuntimeError("the watcher failed after creating the job")
    with db.transaction() as s:
        assert s.scalar(select(func.count()).select_from(Job)) == 1
        assert s.scalar(select(func.count()).select_from(Recording)) == 1
        assert s.scalars(select(JobEvent)).one().actor_name == "anna"


# --- the normal path -------------------------------------------------------------------------


def test_claim_leases_the_oldest_due_job(queue, db):
    first, second = new_job(queue), new_job(queue)
    claim = queue.claim("worker-1")
    assert isinstance(claim, Claim)
    assert (claim.job_id, claim.stage, claim.attempt) == (first, Stage.IMPORT, 1)
    assert claim.lease_expires_at == T0 + timedelta(seconds=300)
    job = queue.get(first)
    assert (job.status, job.lease_owner, job.attempts) == (JobStatus.RUNNING, "worker-1", 1)
    assert runs(db, first) == [(Stage.IMPORT, 1, StageRunStatus.RUNNING)]
    assert last_event(db, first).kind == EventKind.STARTED
    assert queue.claim("worker-2").job_id == second
    assert queue.claim("worker-3") is None


def test_a_job_runs_every_stage_in_order_until_done(queue, db, clock):
    job_id = new_job(queue)
    stages = []
    while (claim := queue.claim("w1")) is not None:
        stages.append(claim.stage)
        clock.advance(10)
        queue.complete(claim, metrics={"compute_seconds": 9.5}, message="ok")
    assert stages == list(PIPELINE)
    job = queue.get(job_id)
    assert (job.status, job.stage, job.lease_owner) == (JobStatus.DONE, Stage.POST, None)
    assert job.finished_at == T0 + timedelta(seconds=70)
    assert runs(db, job_id) == [(stage, 1, StageRunStatus.SUCCEEDED) for stage in PIPELINE]
    expected = ["created"] + ["started", "stage_completed"] * 6 + ["started", "done"]
    assert kinds(db, job_id) == expected
    with db.transaction() as s:
        run = s.scalars(select(StageRun).where(StageRun.job_id == job_id)).first()
        assert run.metrics == {"compute_seconds": 9.5}
        assert run.duration_seconds == 10
    assert last_event(db, job_id).message == "Posting links finished; all stages done. ok"


def test_heartbeat_extends_the_lease(queue, clock):
    new_job(queue)
    claim = queue.claim("w1")
    clock.advance(200)
    assert queue.heartbeat(claim) == T0 + timedelta(seconds=500)
    assert queue.get(claim.job_id).lease_expires_at == T0 + timedelta(seconds=500)


# --- failures and retries --------------------------------------------------------------------


def test_backoff_delays():
    policy = RetryPolicy(backoff_base_seconds=60, backoff_factor=4, backoff_max_seconds=3600)
    delays = [policy.delay(attempt).total_seconds() for attempt in range(1, 6)]
    assert delays == [60, 240, 960, 3600, 3600]  # base × factor^(attempt − 1), capped
    assert policy.delay(10_000).total_seconds() == 3600  # no overflow
    with pytest.raises(ValueError):
        policy.delay(0)


def test_policy_and_lease_come_from_the_settings(db):
    workers = Settings().workers
    queue = JobQueue.from_settings(db, workers)
    assert queue.policy == RetryPolicy(3, 60.0, 4.0, 3600.0)
    assert queue.lease_seconds == 300.0
    changed = workers.model_copy(update={"max_attempts": 5, "lease_seconds": 60.0})
    queue.apply_settings(changed)
    assert (queue.policy.max_attempts, queue.lease_seconds) == (5, 60.0)


def test_a_failing_stage_is_retried_with_backoff_then_fails(queue, db, clock):
    job_id = new_job(queue)
    queue.fail(queue.claim("w1"), "network down 1")
    job = queue.get(job_id)
    assert (job.status, job.attempts) == (JobStatus.QUEUED, 1)
    assert job.next_attempt_at == T0 + timedelta(seconds=60)
    assert job.last_error == "network down 1"

    clock.advance(59)
    assert queue.claim("w1") is None  # not due yet
    clock.advance(1)
    second = queue.claim("w1")
    assert second.attempt == 2
    queue.fail(second, "network down 2")
    assert queue.get(job_id).next_attempt_at == clock.now + timedelta(seconds=240)

    clock.advance(240)
    queue.fail(queue.claim("w1"), "network down 3")
    job = queue.get(job_id)
    assert (job.status, job.stage, job.attempts) == (JobStatus.FAILED, Stage.IMPORT, 3)
    assert job.last_error == "network down 3"
    assert job.finished_at == clock.now
    assert runs(db, job_id) == [(Stage.IMPORT, n, StageRunStatus.FAILED) for n in (1, 2, 3)]
    assert kinds(db, job_id)[1:] == [
        "started",
        "retry_scheduled",
        "started",
        "retry_scheduled",
        "started",
        "failed",
    ]
    assert "all 3 attempts used" in last_event(db, job_id).message
    assert queue.claim("w1") is None


def test_a_fatal_error_fails_at_once(queue, db):
    job_id = new_job(queue)
    queue.fail(queue.claim("w1"), "file is not a video", retryable=False)
    job = queue.get(job_id)
    assert (job.status, job.attempts) == (JobStatus.FAILED, 1)
    assert "not retryable" in last_event(db, job_id).message


def test_long_errors_are_cut(queue):
    job_id = new_job(queue)
    queue.fail(queue.claim("w1"), "x" * 10_000)
    assert len(queue.get(job_id).last_error) == 4000


def test_retry_now_skips_the_waiting_time(queue, db):
    job_id = new_job(queue)
    queue.fail(queue.claim("w1"), "temporary")
    with pytest.raises(InvalidTransition, match="not waiting"):
        queue.retry_now(new_job(queue), "anna")
    assert queue.retry_now(job_id, "anna").next_attempt_at is None
    event = last_event(db, job_id)
    assert (event.kind, event.actor_kind, event.actor_name) == ("retry_now", "staff", "anna")
    assert queue.claim("w1").job_id == job_id


# --- holds, release, cancel, re-run ----------------------------------------------------------


def test_hold_then_release_runs_the_stage_again(queue, db):
    job_id = new_job(queue, Stage.CHAPTERS)
    queue.hold(queue.claim("w1"), "identification_missing", "group of the folder is unknown")
    job = queue.get(job_id)
    assert (job.status, job.hold_reason) == (JobStatus.ON_HOLD, "identification_missing")
    assert job.hold_message == "group of the folder is unknown"
    assert job.lease_owner is None
    assert runs(db, job_id) == [(Stage.CHAPTERS, 1, StageRunStatus.HELD)]
    assert queue.claim("w1") is None  # nothing runs while on hold

    job = queue.release(job_id, "anna", note="group set to Python 12")
    assert (job.status, job.stage, job.attempts) == (JobStatus.QUEUED, Stage.CHAPTERS, 0)
    assert job.hold_reason is None
    event = last_event(db, job_id)
    assert (event.kind, event.actor_kind, event.actor_name) == ("released", "staff", "anna")
    assert event.data == {
        "reason": "identification_missing",
        "advance": False,
        "note": "group set to Python 12",
    }
    assert queue.claim("w1").stage == Stage.CHAPTERS


def test_release_with_advance_continues_with_the_next_stage(queue):
    job_id = new_job(queue, Stage.CONSENT)
    queue.hold(queue.claim("w1"), "consent_opted_out", "1 student opted out")
    job = queue.release(job_id, "anna", advance=True, note="student not on camera")
    assert (job.status, job.stage) == (JobStatus.QUEUED, Stage.UPLOAD)


def test_release_with_advance_at_the_last_stage_finishes_the_job(queue):
    job_id = new_job(queue, Stage.POST)
    queue.hold(queue.claim("w1"), "check_post")
    assert queue.release(job_id, "anna", advance=True).status is JobStatus.DONE


def test_hold_reason_must_be_a_code(queue):
    new_job(queue)
    claim = queue.claim("w1")
    with pytest.raises(ValueError, match="hold reason"):
        queue.hold(claim, "Consent opted out")


@pytest.mark.parametrize("state", ["queued", "on_hold", "failed", "running"])
def test_cancel(queue, db, state):
    job_id = new_job(queue)
    claim = queue.claim("w1") if state != "queued" else None
    if state == "on_hold":
        queue.hold(claim, "consent_unreachable")
    elif state == "failed":
        queue.fail(claim, "bad file", retryable=False)
    job = queue.cancel(job_id, "anna", note="wrong file")
    assert job.status is JobStatus.CANCELLED
    assert job.finished_at == T0
    assert last_event(db, job_id).data == {"previous_status": state, "note": "wrong file"}
    if state == "running":
        assert job.lease_owner is None
        assert runs(db, job_id) == [(Stage.IMPORT, 1, StageRunStatus.CANCELLED)]
        with pytest.raises(LeaseLost):  # the worker learns it must stop ...
            queue.heartbeat(claim)
        with pytest.raises(LeaseLost):  # ... and its result is not used
            queue.complete(claim)
        assert queue.get(job_id).status is JobStatus.CANCELLED
    assert queue.claim("w1") is None


def test_rerun_from_the_failed_stage_does_not_repeat_earlier_stages(queue, db):
    """UJ-4: a stage fails, the cause is fixed, staff re-run from that stage."""
    job_id = new_job(queue)
    for _ in range(2):  # import and transcribe succeed
        queue.complete(queue.claim("w1"))
    queue.fail(queue.claim("w1"), "renderer crashed", retryable=False)
    assert queue.get(job_id).stage == Stage.CLEAN

    job = queue.rerun(job_id, "anna", Stage.CLEAN, note="fixed the renderer")
    assert (job.status, job.stage, job.attempts) == (JobStatus.QUEUED, Stage.CLEAN, 0)
    assert job.last_error is None and job.finished_at is None
    assert run_to_done(queue) == list(PIPELINE[2:])
    stages_run = [stage for stage, _, _ in runs(db, job_id)]
    assert stages_run.count(Stage.IMPORT) == 1
    assert stages_run.count(Stage.TRANSCRIBE) == 1
    assert stages_run.count(Stage.CLEAN) == 2
    assert queue.get(job_id).status is JobStatus.DONE


def test_rerun_of_a_done_job_from_an_earlier_stage(queue, db):
    """UJ-5: a wrong cut is reported; staff change settings and re-run from cleaning."""
    job_id = new_job(queue)
    run_to_done(queue)
    job = queue.rerun(job_id, "anna", "clean")
    assert (job.status, job.stage, job.finished_at) == (JobStatus.QUEUED, Stage.CLEAN, None)
    assert run_to_done(queue) == list(PIPELINE[2:])
    assert queue.get(job_id).status is JobStatus.DONE
    event = [e for e in kinds(db, job_id) if e == "rerun"]
    assert event == ["rerun"]


def test_rerun_cannot_skip_stages(queue):
    job_id = new_job(queue)
    queue.complete(queue.claim("w1"))  # now queued at transcribe
    with pytest.raises(InvalidTransition, match="stages would be skipped"):
        queue.rerun(job_id, "anna", Stage.CHAPTERS)
    assert queue.rerun(job_id, "anna", Stage.IMPORT).stage == Stage.IMPORT


def test_rerun_of_a_cancelled_job(queue):
    job_id = new_job(queue)
    queue.cancel(job_id, "anna")
    assert queue.rerun(job_id, "anna", Stage.IMPORT).status is JobStatus.QUEUED


# --- crashes and stopping workers ------------------------------------------------------------


def test_expired_lease_is_recovered_and_the_job_later_completes(queue, db, clock):
    """A worker crashes mid-stage; the job is given back and finishes later (NFR-2)."""
    job_id = new_job(queue)
    crashed = queue.claim("worker-1")
    clock.advance(299)
    assert queue.recover_expired() == []  # the lease is still valid
    clock.advance(2)
    (recovered,) = queue.recover_expired()
    assert (recovered.id, recovered.status, recovered.attempts) == (job_id, JobStatus.QUEUED, 1)
    # Counts as failed attempt 1: the backoff runs from the lease expiry (T0 + 300 s).
    assert recovered.next_attempt_at == T0 + timedelta(seconds=300 + 60)
    assert "worker-1 stopped responding" in recovered.last_error
    event = last_event(db, job_id)
    assert (event.kind, event.actor_kind) == ("lease_expired", ActorKind.SYSTEM)
    assert event.data["worker"] == "worker-1"
    assert runs(db, job_id) == [(Stage.IMPORT, 1, StageRunStatus.INTERRUPTED)]

    with pytest.raises(LeaseLost):  # the crashed worker's late result is ignored
        queue.complete(crashed)

    clock.advance(60)
    claim = queue.claim("worker-2")
    assert (claim.job_id, claim.attempt) == (job_id, 2)
    queue.complete(claim)
    assert run_to_done(queue, "worker-2") == list(PIPELINE[1:])
    assert queue.get(job_id).status is JobStatus.DONE


def test_claim_recovers_expired_leases_by_itself(queue, clock):
    job_id = new_job(queue)
    queue.claim("worker-1")
    clock.advance(301)
    assert queue.claim("worker-2") is None  # recovered, but waiting for its backoff
    assert queue.get(job_id).status is JobStatus.QUEUED
    clock.advance(60)
    assert queue.claim("worker-2").job_id == job_id


def test_leases_of_workers_known_to_be_alive_are_not_recovered(queue, clock):
    """After the PC slept longer than a lease, the worker's own running job stays its own
    (D-056); a lease of any other worker is still recovered."""
    first, second = new_job(queue), new_job(queue)
    queue.claim("pc-1")
    queue.claim("pc-2")
    clock.advance(10 * 3600)
    # pc-2 was not in keep: its job is recovered and, its backoff long over, taken again
    assert queue.claim("pc-3", keep=("pc-1",)).job_id == second
    assert queue.get(first).status is JobStatus.RUNNING
    assert queue.get(first).lease_owner == "pc-1"
    assert queue.claim("pc-4").job_id == first  # without keep, recovered as before


def test_lease_expiry_on_the_last_attempt_fails_the_job(db, clock):
    queue = JobQueue(db, policy=RetryPolicy(max_attempts=1), lease_seconds=60, clock=clock)
    job_id = new_job(queue)
    queue.claim("worker-1")
    clock.advance(61)
    (job,) = queue.recover_expired()
    assert (job.id, job.status) == (job_id, JobStatus.FAILED)
    assert "all 1 attempts used: failed" in last_event(db, job_id).message


def test_backoff_after_a_crash_counts_from_the_lease_expiry(queue, db, clock):
    """The only worker crashed and came back an hour later: no extra waiting."""
    job_id = new_job(queue)
    queue.claim("worker-1")
    clock.advance(3600)
    claim = queue.claim("worker-1")
    assert (claim.job_id, claim.attempt) == (job_id, 2)
    with db.transaction() as s:
        expired = s.scalars(
            select(JobEvent).where(JobEvent.kind == "lease_expired", JobEvent.job_id == job_id)
        ).one()
        assert expired.message.endswith("can be tried again now")
        run = s.scalars(select(StageRun).where(StageRun.job_id == job_id)).first()
        assert run.finished_at == T0 + timedelta(seconds=300)  # when the lease ran out


def test_an_old_claim_cannot_touch_a_newer_attempt_of_the_same_worker(queue, clock):
    job_id = new_job(queue)
    old = queue.claim("worker-1")
    clock.advance(300 + 60)
    new = queue.claim("worker-1")  # recovered, then claimed again by the same worker name
    assert (new.job_id, new.attempt) == (job_id, 2)
    for call in (queue.heartbeat, queue.complete, queue.interrupt):
        with pytest.raises(LeaseLost):
            call(old)
    with pytest.raises(LeaseLost):
        queue.fail(old, "late")
    with pytest.raises(LeaseLost):
        queue.hold(old, "late")
    queue.complete(new)
    assert queue.get(job_id).stage == Stage.TRANSCRIBE


def test_interrupt_gives_the_job_back_without_using_an_attempt(queue, db):
    job_id = new_job(queue)
    job = queue.interrupt(queue.claim("w1"), "worker shutting down")
    assert (job.status, job.attempts, job.next_attempt_at) == (JobStatus.QUEUED, 0, None)
    assert runs(db, job_id) == [(Stage.IMPORT, 1, StageRunStatus.INTERRUPTED)]
    assert queue.claim("w2").attempt == 1


# --- invalid operations ----------------------------------------------------------------------


def job_in(queue: JobQueue, status: str) -> int:
    job_id = new_job(queue)
    if status == "queued":
        return job_id
    claim = queue.claim("setup")
    if status == "on_hold":
        queue.hold(claim, "consent_unreachable")
    elif status == "failed":
        queue.fail(claim, "broken", retryable=False)
    elif status == "done":
        queue.complete(claim)
        run_to_done(queue, "setup")
    elif status == "cancelled":
        queue.cancel(job_id, "anna")
    return job_id


INVALID = [
    ("release", "queued"),
    ("release", "running"),
    ("release", "failed"),
    ("release", "done"),
    ("release", "cancelled"),
    ("cancel", "done"),
    ("cancel", "cancelled"),
    ("rerun", "running"),
    ("retry_now", "running"),
    ("retry_now", "on_hold"),
    ("retry_now", "failed"),
    ("retry_now", "done"),
    ("retry_now", "cancelled"),
]


@pytest.mark.parametrize(("action", "status"), INVALID)
def test_invalid_transitions_are_rejected_and_change_nothing(queue, db, action, status):
    job_id = job_in(queue, status)
    before, events_before = queue.get(job_id), kinds(db, job_id)
    calls = {
        "release": lambda: queue.release(job_id, "anna"),
        "cancel": lambda: queue.cancel(job_id, "anna"),
        "rerun": lambda: queue.rerun(job_id, "anna", Stage.IMPORT),
        "retry_now": lambda: queue.retry_now(job_id, "anna"),
    }
    with pytest.raises(InvalidTransition, match=f"it is {status.replace('_', ' ')}"):
        calls[action]()
    assert queue.get(job_id) == before
    assert kinds(db, job_id) == events_before


def test_unknown_job(queue):
    for call in (
        lambda: queue.get(42),
        lambda: queue.release(42, "anna"),
        lambda: queue.cancel(42, "anna"),
        lambda: queue.rerun(42, "anna", Stage.IMPORT),
        lambda: queue.retry_now(42, "anna"),
    ):
        with pytest.raises(JobNotFound):
            call()


def test_names_are_required(queue):
    job_id = new_job(queue)
    with pytest.raises(ValueError, match="worker"):
        queue.claim("  ")
    with pytest.raises(ValueError, match="staff user"):
        queue.cancel(job_id, "")


def test_the_clock_must_give_aware_times(db):
    queue = JobQueue(db, clock=lambda: datetime(2026, 11, 18, 14, 0))
    with pytest.raises(ValueError, match="time-zone-aware"):
        queue.enqueue(new_recording(db))


# --- limits and concurrency ------------------------------------------------------------------


def test_heavy_stage_limit_is_applied_when_claiming(queue):
    first = new_job(queue, Stage.TRANSCRIBE)
    second = new_job(queue, Stage.TRANSCRIBE)
    light = new_job(queue, Stage.POST)

    def claim():
        return queue.claim("w", heavy_stages=HEAVY, heavy_limit=1)

    running = claim()
    assert running.job_id == first
    assert claim().job_id == light  # the second heavy job must wait
    assert claim() is None
    queue.complete(running)  # the first job moves on to cleaning (also heavy)
    assert claim().job_id == first  # oldest first; no heavy stage is running now
    assert claim() is None
    assert queue.get(second).status is JobStatus.QUEUED


def test_claim_can_be_limited_to_some_stages(queue):
    new_job(queue, Stage.IMPORT)
    upload = new_job(queue, Stage.UPLOAD)
    assert queue.claim("uploader", stages=[Stage.UPLOAD, Stage.POST]).job_id == upload
    assert queue.claim("uploader", stages=[Stage.UPLOAD, Stage.POST]) is None
    assert queue.claim("uploader", stages=[]) is None


def test_concurrent_threads_never_claim_the_same_job(db):
    queue = JobQueue(db)
    job_ids = [new_job(queue) for _ in range(12)]
    claimed: list[tuple[int, str]] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def work(name: str) -> None:
        try:
            while (claim := queue.claim(name)) is not None:
                with lock:
                    claimed.append((claim.job_id, str(claim.stage)))
                time.sleep(0.002)  # some "work", so the threads overlap
                queue.complete(claim)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(f"t{n}",)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(claimed) == len(set(claimed)) == len(job_ids) * len(PIPELINE)
    assert {queue.get(job_id).status for job_id in job_ids} == {JobStatus.DONE}


WORKER_PROCESS = r"""
import json, pathlib, sys, time
from reel.db import open_database
from reel.queue import JobQueue

db_path, name, folder = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
db = open_database(db_path)
queue = JobQueue(db)
(folder / f"ready-{name}").touch()
while not (folder / "go").exists():
    time.sleep(0.01)
claimed = []
while (claim := queue.claim(name)) is not None:
    claimed.append([claim.job_id, str(claim.stage)])
    time.sleep(0.002)
    queue.complete(claim)
db.dispose()
print(json.dumps(claimed))
"""


def test_separate_processes_never_claim_the_same_job(db, tmp_path):
    queue = JobQueue(db)
    job_ids = [new_job(queue) for _ in range(8)]
    names = ["p1", "p2", "p3"]
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER_PROCESS, str(db.path), name, str(tmp_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for name in names
    ]
    try:
        deadline = time.monotonic() + 60
        while not all((tmp_path / f"ready-{name}").exists() for name in names):
            assert time.monotonic() < deadline, "worker processes did not start"
            assert all(p.poll() is None for p in processes), "a worker process exited early"
            time.sleep(0.05)
        (tmp_path / "go").touch()
        outputs = [p.communicate(timeout=120) for p in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
    for process, (_out, err) in zip(processes, outputs, strict=True):
        assert process.returncode == 0, err
    claimed = [tuple(item) for out, _ in outputs for item in json.loads(out)]
    assert len(claimed) == len(set(claimed)) == len(job_ids) * len(PIPELINE)
    assert {queue.get(job_id).status for job_id in job_ids} == {JobStatus.DONE}


# --- history and logs ------------------------------------------------------------------------


def test_history_is_kept_through_every_kind_of_transition(queue, db, clock):
    job_id = new_job(queue, Stage.CONSENT)
    queue.fail(queue.claim("w1"), "core engine timeout")  # retry scheduled
    clock.advance(60)
    queue.hold(queue.claim("w1"), "consent_opted_out")
    queue.release(job_id, "anna", advance=True)
    queue.complete(queue.claim("w2"))  # upload
    queue.cancel(job_id, "anna")
    queue.rerun(job_id, "anna", Stage.POST)
    queue.complete(queue.claim("w2"))
    assert kinds(db, job_id) == [
        "created",
        "started",
        "retry_scheduled",
        "started",
        "held",
        "released",
        "started",
        "stage_completed",
        "cancelled",
        "rerun",
        "started",
        "done",
    ]
    assert runs(db, job_id) == [
        (Stage.CONSENT, 1, StageRunStatus.FAILED),
        (Stage.CONSENT, 2, StageRunStatus.HELD),
        (Stage.UPLOAD, 1, StageRunStatus.SUCCEEDED),
        (Stage.POST, 1, StageRunStatus.SUCCEEDED),
    ]
    with db.transaction() as s:
        actors = [
            (e.kind, e.actor_kind, e.actor_name)
            for e in s.scalars(select(JobEvent).where(JobEvent.job_id == job_id))
        ]
    assert ("held", ActorKind.WORKER, "w1") in actors
    assert ("released", ActorKind.STAFF, "anna") in actors
    assert ("created", ActorKind.SYSTEM, None) in actors


def test_transitions_are_logged_with_the_job_id(queue, caplog):
    caplog.set_level(logging.INFO, logger="reel.queue")
    job_id = new_job(queue)
    queue.complete(queue.claim("w1"))
    records = [r for r in caplog.records if r.name == "reel.queue"]
    assert [r.event for r in records] == ["created", "started", "stage_completed"]
    assert {r.job_id for r in records} == {job_id}
    assert records[-1].getMessage() == "Imported finished; next: Transcribed"


def test_stage_runs_and_events_survive_a_new_queue_object(queue, db, clock):
    """A restart = a new JobQueue on the same database; nothing is lost."""
    job_id = new_job(queue)
    queue.complete(queue.claim("w1"))
    restarted = JobQueue(db, clock=clock)
    assert restarted.get(job_id).stage == Stage.TRANSCRIBE
    assert restarted.claim("w9").stage == Stage.TRANSCRIBE
    assert len(runs(db, job_id)) == 2
