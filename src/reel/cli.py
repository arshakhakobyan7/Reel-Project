"""Command-line interface for Reel.

Each command group registers itself here; later tasks add worker, serve, db, user, ...
"""

import argparse
import os
import sys
from collections.abc import Sequence

from reel import __version__
from reel.auth import commands as auth_commands
from reel.config import commands as config_commands
from reel.db import commands as db_commands
from reel.ingest import commands as watch_commands
from reel.logging_setup import configure_logging
from reel.queue import admin as job_commands
from reel.queue import commands as worker_commands
from reel.web import commands as web_commands


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level ``reel`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="reel",
        description="Reel - lesson recordings edited and published automatically.",
    )
    parser.add_argument("--version", action="version", version=f"reel {__version__}")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="configuration file (default: $REEL_CONFIG, else ./config/reel.toml)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show debug messages in the terminal"
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    config_commands.register(commands)
    db_commands.register(commands)
    worker_commands.register(commands)
    job_commands.register(commands)
    watch_commands.register(commands)
    auth_commands.register(commands)
    web_commands.register(commands)
    return parser


def use_utf8_output() -> None:
    """Print Armenian text safely even when output is redirected to a file or pipe.

    On Windows a redirected stream uses the legacy code page, which cannot encode Armenian.
    """
    # Started without a console (pythonw.exe, e.g. by Task Scheduler, T-903): there are no
    # streams at all. Output then goes nowhere; the log files still get everything.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    use_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    # Short commands log to the terminal only; long-running ones (worker, serve) switch to
    # configure_from_config(), which adds the JSON and per-job log files.
    configure_logging(console_level="DEBUG" if args.verbose else "WARNING")
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)
