"""``reel watch``: queue new recordings from the watch folders (Ctrl+C stops)."""

import argparse
import sys
import threading

from reel.config.loader import ConfigError, ConfigProvider, find_config_file
from reel.db import DatabaseError, open_database
from reel.ingest.watcher import FolderWatcher
from reel.logging_setup import configure_from_config
from reel.queue.commands import stop_on_signals


def register(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("watch", help="queue new recordings from the watch folders")
    parser.add_argument("--once", action="store_true", help="scan once and print what was found")
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    try:
        provider = ConfigProvider(find_config_file(args.config))
        cfg = provider.get()
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    configure_from_config(cfg, role="watcher", console_level="DEBUG" if args.verbose else None)
    try:
        db = open_database(cfg.paths.database_file)
    except DatabaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    with db:
        watcher = FolderWatcher(db, provider.get)
        if args.once:
            result = watcher.scan()
            jobs = ", ".join(map(str, result.queued)) or "none"
            print(
                f"{result.files} file(s): {result.waiting} waiting until fully written, "
                f"{len(result.queued)} queued (jobs: {jobs}), {result.gone} deleted early"
            )
            return 0
        stop = threading.Event()
        with stop_on_signals(stop.set):
            thread = threading.Thread(target=watcher.run, args=(stop,), daemon=True)
            thread.start()
            while thread.is_alive():
                thread.join(0.5)
    return 0
