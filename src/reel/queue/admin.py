"""Job commands for the terminal (T-901): ``reel status``, ``rerun``, ``release``, ``cancel``,
``retry``. The actions are the dashboard's (T-804) and are audited with ``--by`` (default:
the Windows user name)."""

import argparse
import getpass
import sys
from collections.abc import Callable
from pathlib import PureWindowsPath

from sqlalchemy import func, select

from reel.config.loader import ConfigError, ConfigProvider, find_config_file
from reel.db import (
    Database,
    DatabaseError,
    Job,
    JobEvent,
    PublicationTarget,
    Recording,
    Stage,
    StageRun,
    open_database,
)
from reel.db.enums import JobStatus
from reel.queue.jobs import InvalidTransition, JobNotFound, JobQueue


def _open(args: argparse.Namespace) -> Database:
    cfg = ConfigProvider(find_config_file(args.config)).get()
    return open_database(cfg.paths.database_file)


def _actor(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--by", default=getpass.getuser(), help="your name for the audit log")
    parser.add_argument("--note", default="", help="why (kept in the audit log)")


def register(commands: argparse._SubParsersAction) -> None:
    status = commands.add_parser("status", help="show jobs (all, or one in detail)")
    status.add_argument("job", nargs="?", type=int, help="a job id for its details")
    status.add_argument("--status", choices=[s.value for s in JobStatus], help="only this status")
    status.add_argument("--limit", type=int, default=20, help="how many jobs (newest first)")
    status.set_defaults(handler=run_status)

    rerun = commands.add_parser("rerun", help="run a job again from a stage")
    rerun.add_argument("job", type=int)
    rerun.add_argument("--from", dest="stage", required=True, choices=[s.value for s in Stage])
    _actor(rerun)
    rerun.set_defaults(handler=run_action, action="rerun")

    release = commands.add_parser("release", help="release a job on hold")
    release.add_argument("job", type=int)
    release.add_argument(
        "--accept", action="store_true", help="accept the held stage and go on (else: run it again)"
    )
    _actor(release)
    release.set_defaults(handler=run_action, action="release")

    cancel = commands.add_parser("cancel", help="cancel a job (nothing is published)")
    cancel.add_argument("job", type=int)
    _actor(cancel)
    cancel.set_defaults(handler=run_action, action="cancel")

    retry = commands.add_parser("retry", help="try a job waiting for a retry now")
    retry.add_argument("job", type=int)
    _actor(retry)
    retry.set_defaults(handler=run_action, action="retry")

    cleanup = commands.add_parser("cleanup", help="remove expired working files (retention)")
    cleanup.add_argument("--dry-run", action="store_true", help="only list what would go")
    cleanup.set_defaults(handler=run_cleanup)


def _title(recording: Recording) -> str:
    name = PureWindowsPath(recording.source_path).name
    if recording.group_name and recording.lesson_number is not None:
        return f"{recording.group_name} · lesson {recording.lesson_number}"
    return f"{recording.group_name} · {name}" if recording.group_name else name


def _when(moment) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else "-"


def run_status(args: argparse.Namespace) -> int:
    try:
        db = _open(args)
    except (ConfigError, DatabaseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    with db, db.transaction() as s:
        if args.job is not None:
            return _one(s, args.job)
        counts = dict(s.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        print("Jobs: " + ", ".join(f"{st.label.lower()} {counts.get(st, 0)}" for st in JobStatus))
        query = select(Job, Recording).join(Recording, Job.recording_id == Recording.id)
        if args.status:
            query = query.where(Job.status == JobStatus(args.status))
        rows = s.execute(query.order_by(Job.id.desc()).limit(max(args.limit, 1))).all()
        for job, recording in rows:
            line = f"#{job.id:<5} {job.status.label:<10} {job.stage.label:<14} {_title(recording)}"
            problem = job.hold_message or job.last_error
            if problem and job.status is not JobStatus.DONE:
                line += f"  — {' '.join(problem.split())[:120]}"
            print(line)
        if not rows:
            print("No jobs.")
    return 0


def _one(s, job_id: int) -> int:
    job = s.get(Job, job_id)
    if job is None:
        print(f"ERROR: job {job_id} does not exist", file=sys.stderr)
        return 1
    recording = job.recording
    print(f"Job #{job.id}: {_title(recording)}")
    print(f"  File      : {recording.source_path}")
    print(f"  Status    : {job.status.label} at {job.stage.label} (attempts {job.attempts})")
    if job.hold_reason:
        print(f"  On hold   : {job.hold_reason} — {job.hold_message or ''}")
    if job.last_error:
        print(f"  Last error: {job.last_error}")
    if job.next_attempt_at:
        print(f"  Next try  : {_when(job.next_attempt_at)} UTC")
    if job.flags:
        print(f"  Flags     : {', '.join(job.flags)}")
    print("  Stage runs:")
    for run in s.scalars(select(StageRun).where(StageRun.job_id == job.id).order_by(StageRun.id)):
        error = f" — {run.error[:100]}" if run.error else ""
        print(f"    {run.stage.label:<14} #{run.attempt} {run.status.value:<11} "
              f"{_when(run.started_at)}{error}")  # fmt: skip
    publications = s.scalars(
        select(PublicationTarget).where(PublicationTarget.job_id == job.id)
    ).all()
    if publications:
        print("  Publications:")
        for row in publications:
            mode = " (dry-run)" if row.dry_run else ""
            print(f"    {row.target.value:<10} {row.status.value}{mode} {row.url or ''}")
    print("  Last events:")
    events = s.scalars(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.id.desc()).limit(8)
    ).all()
    for event in reversed(events):
        print(f"    {_when(event.at)} {event.kind:<16} {event.message[:110]}")
    return 0


def run_cleanup(args: argparse.Namespace) -> int:
    from reel.retention import cleanup

    try:
        cfg = ConfigProvider(find_config_file(args.config)).get()
        db = open_database(cfg.paths.database_file)
        with db:
            report = cleanup(db, cfg, dry_run=args.dry_run)
    except (ConfigError, DatabaseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    verb = "Would remove" if args.dry_run else "Removed"
    for removal in report.removals:
        size = f" ({removal.bytes / 1024**2:.0f} MB)" if removal.bytes else ""
        print(f"{verb}: {removal.detail}{size}")
    for kept in report.kept:
        print(f"Kept: {kept}")
    for error in report.errors:
        print(f"ERROR: could not remove {error}", file=sys.stderr)
    total = report.bytes / 1024**3
    print(f"{verb} {len(report.removals)} item(s), {total:.2f} GB.")
    return 1 if report.errors else 0


def run_action(args: argparse.Namespace) -> int:
    actions: dict[str, Callable[[JobQueue], object]] = {
        "rerun": lambda q: q.rerun(args.job, args.by, Stage(args.stage), note=args.note),
        "release": lambda q: q.release(args.job, args.by, advance=args.accept, note=args.note),
        "cancel": lambda q: q.cancel(args.job, args.by, note=args.note),
        "retry": lambda q: q.retry_now(args.job, args.by),
    }
    try:
        db = _open(args)
        with db:
            job = actions[args.action](JobQueue(db))
    except (ConfigError, DatabaseError, JobNotFound, InvalidTransition, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Job #{job.id}: {job.status.label} at {job.stage.label}.")
    return 0
