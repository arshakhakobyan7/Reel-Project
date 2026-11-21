"""Schema version of the Reel database: create, inspect, upgrade, back up (D-027).

- ``init_database`` creates the database with the current schema and a random
  ``instance_id``. Running it again on an initialised database does nothing.
- ``inspect_database`` reads the state without changing the file (read-only connection).
- ``open_database`` opens a database for work; it must have exactly the schema version of
  this code, so an old or newer schema is never used by mistake.
- ``upgrade_database`` backs the file up, then applies the migrations one version at a
  time, each in its own transaction (SQLite DDL is transactional), then creates tables
  that are new in this version of the code.
"""

import sqlite3
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Connection

from reel import __version__
from reel.db.base import Base, utcnow
from reel.db.engine import DEFAULT_BUSY_TIMEOUT, Database, DatabaseError
from reel.db.models import AppMeta, SchemaVersion

SCHEMA_VERSION = 2
VERSION_TABLE = SchemaVersion.__tablename__


@dataclass(frozen=True)
class Migration:
    """Changes the schema from ``version - 1`` to ``version``.

    A migration that needs a new table creates it itself; tables that are simply new in
    the code are created after the last migration.
    """

    version: int
    description: str
    apply: Callable[[Connection], None]


def _accounts_and_owners(connection: Connection) -> None:
    """Version 2 (T-1013): roles and contact details of the accounts; who uploaded a
    recording and whether it is published. The accounts so far were all staff with every
    right, so they become admins; uploads made so far are linked to their accounts."""
    for statement in (
        "ALTER TABLE staff_users ADD COLUMN role VARCHAR(16) DEFAULT 'user' NOT NULL",
        "ALTER TABLE staff_users ADD COLUMN can_publish BOOLEAN DEFAULT 0 NOT NULL",
        "ALTER TABLE staff_users ADD COLUMN first_name VARCHAR(100)",
        "ALTER TABLE staff_users ADD COLUMN last_name VARCHAR(100)",
        "ALTER TABLE staff_users ADD COLUMN email VARCHAR(254)",
        "ALTER TABLE staff_users ADD COLUMN phone VARCHAR(32)",
        "UPDATE staff_users SET role = 'admin'",
        "CREATE UNIQUE INDEX ix_staff_users_email ON staff_users (email)",
        "ALTER TABLE recordings ADD COLUMN uploaded_by_id INTEGER "
        "REFERENCES staff_users (id) ON DELETE SET NULL",
        "ALTER TABLE recordings ADD COLUMN publish BOOLEAN DEFAULT 1 NOT NULL",
        "CREATE INDEX ix_recordings_uploaded_by_id ON recordings (uploaded_by_id)",
        "UPDATE recordings SET uploaded_by_id = (SELECT staff_users.id FROM staff_users "
        "WHERE staff_users.username = json_extract(recordings.identification, '$.staff.by')) "
        "WHERE json_extract(recordings.identification, '$.staff.source') = 'upload'",
    ):
        connection.exec_driver_sql(statement)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        2, "accounts: roles, contact details; recordings: uploader, publish", _accounts_and_owners
    ),
)


class SchemaState(StrEnum):
    MISSING = "missing"  # no file
    EMPTY = "empty"  # a file without tables; can be initialised
    CURRENT = "current"
    OUTDATED = "outdated"  # older schema: run `reel db upgrade`
    NEWER = "newer"  # made by a newer Reel; this code must not touch it
    FOREIGN = "foreign"  # has tables, but is not a Reel database
    UNREADABLE = "unreadable"  # not a database, locked, no permission, ...


_SUMMARY = {
    SchemaState.MISSING: "does not exist yet: run `reel db init`",
    SchemaState.EMPTY: "is empty: run `reel db init`",
    SchemaState.CURRENT: "has schema version {version} (current)",
    SchemaState.OUTDATED: (
        "has schema version {version}, this Reel needs {expected}: "
        "run `reel db upgrade` (it makes a backup first)"
    ),
    SchemaState.NEWER: (
        "has schema version {version}, newer than this Reel supports ({expected}): update Reel"
    ),
    SchemaState.FOREIGN: "is not a Reel database: check paths.database_file",
    SchemaState.UNREADABLE: "cannot be read: {error}",
}


@dataclass(frozen=True)
class DatabaseInfo:
    path: Path
    state: SchemaState
    expected_version: int
    version: int | None = None
    instance_id: str | None = None
    created_at: datetime | None = None
    journal_mode: str | None = None
    size_bytes: int = 0
    row_counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def summary(self) -> str:
        """What the state means and what to do, e.g. 'does not exist yet: run ...'."""
        return _SUMMARY[self.state].format(
            version=self.version, expected=self.expected_version, error=self.error
        )

    def problem(self) -> str:
        return f"database {self.path} {self.summary()}"


@dataclass(frozen=True)
class UpgradeResult:
    from_version: int
    to_version: int
    backup: Path | None


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)


def inspect_database(
    path: Path | str, *, expected_version: int = SCHEMA_VERSION, count_rows: bool = False
) -> DatabaseInfo:
    """Describe the database at ``path`` without creating or changing it."""
    path = Path(path).resolve()
    if not path.exists():
        return DatabaseInfo(path, SchemaState.MISSING, expected_version)
    size = path.stat().st_size if path.is_file() else 0

    def info(state: SchemaState, **values: object) -> DatabaseInfo:
        return DatabaseInfo(path, state, expected_version, size_bytes=size, **values)

    if not path.is_file():
        return info(SchemaState.UNREADABLE, error="it is a folder, not a file")
    try:
        connection = _connect_read_only(path)
    except sqlite3.Error as exc:
        return info(SchemaState.UNREADABLE, error=str(exc))
    try:
        tables = {
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not tables:
            return info(SchemaState.EMPTY)
        if VERSION_TABLE not in tables:
            return info(SchemaState.FOREIGN)
        version = connection.execute(f"SELECT max(version) FROM {VERSION_TABLE}").fetchone()[0]
        if version is None:
            return info(SchemaState.FOREIGN)
        meta: dict[str, str] = {}
        if AppMeta.__tablename__ in tables:
            meta = dict(connection.execute(f"SELECT key, value FROM {AppMeta.__tablename__}"))
        counts: dict[str, int] = {}
        if count_rows:
            for table in sorted(tables & set(Base.metadata.tables)):
                counts[table] = connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    except sqlite3.Error as exc:
        return info(SchemaState.UNREADABLE, error=str(exc))
    finally:
        connection.close()

    if version == expected_version:
        state = SchemaState.CURRENT
    elif version < expected_version:
        state = SchemaState.OUTDATED
    else:
        state = SchemaState.NEWER
    created = meta.get("created_at")
    return info(
        state,
        version=version,
        instance_id=meta.get("instance_id"),
        created_at=datetime.fromisoformat(created) if created else None,
        journal_mode=journal_mode,
        row_counts=counts,
    )


def init_database(path: Path | str, *, app_version: str = __version__) -> tuple[bool, DatabaseInfo]:
    """Create the database with the current schema. Returns ``(created, info)``.

    An existing Reel database with the current schema is left as it is. Anything else
    that already exists at ``path`` (another file, an older or newer schema) is an error.
    """
    path = Path(path).resolve()
    before = inspect_database(path)
    if before.state is SchemaState.CURRENT:
        return False, before
    if before.state not in (SchemaState.MISSING, SchemaState.EMPTY):
        raise DatabaseError(before.problem())

    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    with Database(path, create=True) as database, database.begin(immediate=True) as connection:
        # Re-check inside the write lock in case another process initialised it meanwhile.
        existing = connection.exec_driver_sql(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).scalar()
        if not existing:
            Base.metadata.create_all(connection)
            now = utcnow()
            connection.execute(
                insert(SchemaVersion).values(
                    version=SCHEMA_VERSION,
                    applied_at=now,
                    app_version=app_version,
                    description="initial schema",
                )
            )
            connection.execute(
                insert(AppMeta).values(
                    [
                        {"key": "instance_id", "value": uuid.uuid4().hex},
                        {"key": "created_at", "value": now.isoformat()},
                    ]
                )
            )
            created = True
    after = inspect_database(path)
    if after.state is not SchemaState.CURRENT:
        raise DatabaseError(after.problem())
    return created, after


def open_database(
    path: Path | str,
    *,
    expected_version: int = SCHEMA_VERSION,
    busy_timeout: float = DEFAULT_BUSY_TIMEOUT,
    echo: bool = False,
) -> Database:
    """Open the database for work. Its schema must match this code exactly."""
    info = inspect_database(path, expected_version=expected_version)
    if info.state is not SchemaState.CURRENT:
        raise DatabaseError(info.problem())
    return Database(info.path, busy_timeout=busy_timeout, echo=echo)


def backup_database(path: Path | str, folder: Path | str, *, label: str = "backup") -> Path:
    """Copy the database with SQLite's online backup (consistent even while in use)."""
    path = Path(path).resolve()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"{path.stem}-{label}-{stamp}.db"
    counter = 1
    while target.exists():
        target = folder / f"{path.stem}-{label}-{stamp}-{counter}.db"
        counter += 1
    source = _connect_read_only(path)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    return target


def upgrade_database(
    path: Path | str,
    *,
    migrations: Iterable[Migration] = MIGRATIONS,
    expected_version: int = SCHEMA_VERSION,
    app_version: str = __version__,
    backup_folder: Path | str | None = None,
) -> UpgradeResult:
    """Bring an older database to ``expected_version``; a backup is made first."""
    info = inspect_database(path, expected_version=expected_version)
    if info.state is SchemaState.CURRENT:
        return UpgradeResult(expected_version, expected_version, None)
    if info.state is not SchemaState.OUTDATED or info.version is None:
        raise DatabaseError(info.problem())
    steps = {migration.version: migration for migration in migrations}
    needed = range(info.version + 1, expected_version + 1)
    missing = [version for version in needed if version not in steps]
    if missing:
        raise DatabaseError(
            f"database {info.path}: no migration to schema version(s) "
            f"{', '.join(map(str, missing))}"
        )
    backup = backup_database(
        info.path,
        backup_folder or info.path.parent / "backups",
        label=f"v{info.version}",
    )
    with Database(info.path) as database:
        for version in needed:
            with database.begin(immediate=True) as connection:
                current = connection.execute(select(func.max(SchemaVersion.version))).scalar()
                if current is not None and current >= version:
                    continue  # applied by another process meanwhile
                steps[version].apply(connection)
                connection.execute(
                    insert(SchemaVersion).values(
                        version=version,
                        applied_at=utcnow(),
                        app_version=app_version,
                        description=steps[version].description,
                    )
                )
        with database.begin(immediate=True) as connection:
            Base.metadata.create_all(connection)
    return UpgradeResult(info.version, expected_version, backup)
