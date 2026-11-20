"""``reel db init | status | upgrade``."""

import argparse
import sys
from pathlib import Path

from reel.config.loader import ConfigError, find_config_file, load_config
from reel.db.engine import DatabaseError
from reel.db.versioning import (
    SCHEMA_VERSION,
    DatabaseInfo,
    SchemaState,
    init_database,
    inspect_database,
    upgrade_database,
)


def register(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("db", help="create, inspect or upgrade the database")
    actions = parser.add_subparsers(dest="db_action", metavar="<action>", required=True)
    actions.add_parser("init", help="create the database if it does not exist (safe to repeat)")
    actions.add_parser("status", help="show the database file, schema version and row counts")
    actions.add_parser("upgrade", help="back up the database, then apply schema migrations")
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(find_config_file(args.config))
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    path = cfg.paths.database_file
    try:
        if args.db_action == "init":
            return init(path)
        if args.db_action == "upgrade":
            return upgrade(path)
        return status(path)
    except DatabaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def init(path: Path) -> int:
    created, info = init_database(path)
    if created:
        print(f"Created database {info.path} (schema version {info.version}).")
    else:
        print(
            f"Database {info.path} already exists (schema version {info.version}); nothing to do."
        )
    return 0


def upgrade(path: Path) -> int:
    result = upgrade_database(path)
    if result.backup is None:
        print(f"Database {path} is already at schema version {result.to_version}.")
    else:
        print(f"Backup: {result.backup}")
        print(
            f"Upgraded database {path} from schema version {result.from_version} "
            f"to {result.to_version}."
        )
    return 0


def _size(size_bytes: int) -> str:
    return (
        f"{size_bytes / 1024:.1f} KB" if size_bytes < 1024**2 else f"{size_bytes / 1024**2:.1f} MB"
    )


def describe(info: DatabaseInfo) -> list[str]:
    state = str(info.state)
    if info.state is not SchemaState.CURRENT:
        state += f" - {info.summary()}"
    lines = [f"Database      : {info.path}", f"State         : {state}"]
    if info.version is not None:
        lines.append(f"Schema version: {info.version} (this Reel uses {SCHEMA_VERSION})")
    if info.instance_id:
        lines.append(f"Instance id   : {info.instance_id}")
    if info.created_at:
        lines.append(f"Created       : {info.created_at.astimezone().isoformat(' ', 'seconds')}")
    if info.journal_mode:
        lines.append(f"Journal mode  : {info.journal_mode}")
    if info.state is not SchemaState.MISSING:
        lines.append(f"File size     : {_size(info.size_bytes)}")
    if info.row_counts:
        lines.append("Rows          :")
        lines.extend(f"  {table:<20} {count}" for table, count in info.row_counts.items())
    return lines


def status(path: Path) -> int:
    info = inspect_database(path, count_rows=True)
    print("\n".join(describe(info)))
    return 0 if info.state is SchemaState.CURRENT else 1
