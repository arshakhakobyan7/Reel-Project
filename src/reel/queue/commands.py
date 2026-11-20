"""``reel worker``: run pipeline stages from the job queue until stopped (Ctrl+C)."""

import argparse
import logging
import signal
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from reel import stages
from reel.config.loader import ConfigError, ConfigProvider, find_config_file
from reel.db import DatabaseError, open_database
from reel.logging_setup import configure_from_config
from reel.queue.worker import Worker

log = logging.getLogger("reel.worker")


def register(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser(
        "worker", help="run the pipeline stages of queued recordings (Ctrl+C stops)"
    )
    parser.add_argument(
        "--until-idle",
        action="store_true",
        help="stop when no queued or running job is left (for scripts and tests)",
    )
    parser.set_defaults(handler=run)


@contextmanager
def stop_on_signals(stop: Callable[[], None]) -> Iterator[None]:
    """Turn Ctrl+C / Ctrl+Break / SIGTERM into a graceful stop; restore handlers after."""
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            number = getattr(signal, name, None)
            if number is not None:
                previous[number] = signal.signal(number, lambda *_: stop())
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run(args: argparse.Namespace) -> int:
    try:
        provider = ConfigProvider(find_config_file(args.config))
        cfg = provider.get()
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    configure_from_config(cfg, role="worker", console_level="DEBUG" if args.verbose else None)
    try:
        db = open_database(cfg.paths.database_file)
    except DatabaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    registry = stages.default_registry()
    with db:
        worker = Worker(db, registry, provider.get)
        workers = cfg.settings.workers
        log.info(
            "worker started: %d slot(s), heavy-stage limit %d, database %s",
            len(worker.names),
            workers.heavy_stage_limit,
            db.path,
        )
        missing = registry.missing()
        if missing:
            log.warning(
                "no implementation yet for: %s; jobs at these stages wait in the queue",
                ", ".join(stage.label for stage in missing),
            )
        with stop_on_signals(worker.request_stop):
            worker.start(until_idle=args.until_idle)
            while worker.alive:
                worker.join(timeout=0.5)  # short waits keep Ctrl+C responsive
        worker.stop()
        log.info("worker stopped")
    return 0
