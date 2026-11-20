"""SQLite connections, sessions and transactions (D-027).

Every connection uses WAL journaling (readers do not block the writer), enforced foreign
keys, full synchronous commits and a busy timeout. SQLAlchemy emits BEGIN itself instead
of the sqlite3 driver's legacy implicit transactions, so a transaction can start as
``BEGIN IMMEDIATE``: a read-modify-write transaction (the job queue, T-102) then waits for
the write lock up front instead of failing halfway with "database is locked".
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, Connection
from sqlalchemy.orm import Session, sessionmaker

from reel.db.base import json_dumps

DEFAULT_BUSY_TIMEOUT = 30.0  # seconds a connection waits for a lock before giving up
_BEGIN_OPTION = "reel_sqlite_begin"


class DatabaseError(Exception):
    """The database is missing or unreadable, or its schema version does not fit."""


def database_url(path: Path, *, create: bool = False) -> URL:
    """SQLite URI of ``path``.

    Without ``create`` a missing file is an error. A mistyped path then fails clearly
    instead of silently starting a new, empty database.
    """
    return URL.create(
        "sqlite+pysqlite",
        database=Path(path).resolve().as_uri(),
        query={"uri": "true", "mode": "rwc" if create else "rw"},
    )


def _on_connect(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
    dbapi_connection.isolation_level = None  # BEGIN comes from _on_begin
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = FULL")
    finally:
        cursor.close()


def _on_begin(connection: Connection) -> None:
    mode = connection.get_execution_options().get(_BEGIN_OPTION, "DEFERRED")
    connection.exec_driver_sql(f"BEGIN {mode}")


class Database:
    """One Reel database file.

    Use ``transaction()`` for a unit of work (commit on success, rollback on error) and
    ``transaction(immediate=True)`` when the work reads rows and then changes them based
    on what it read. Objects stay usable after the commit (``expire_on_commit=False``).
    """

    def __init__(
        self,
        path: Path | str,
        *,
        create: bool = False,
        busy_timeout: float = DEFAULT_BUSY_TIMEOUT,
        echo: bool = False,
    ) -> None:
        self.path = Path(path).resolve()
        self.engine: Engine = create_engine(
            database_url(self.path, create=create),
            echo=echo,
            connect_args={"timeout": busy_timeout},
            json_serializer=json_dumps,
            json_deserializer=json.loads,
        )
        event.listen(self.engine, "connect", _on_connect)
        event.listen(self.engine, "begin", _on_begin)
        self._immediate_engine = self.engine.execution_options(**{_BEGIN_OPTION: "IMMEDIATE"})
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        self._immediate_sessions = sessionmaker(self._immediate_engine, expire_on_commit=False)

    def __repr__(self) -> str:
        return f"<Database {self.path}>"

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.dispose()

    def session(self, *, immediate: bool = False) -> Session:
        """A new session; the caller commits and closes it."""
        return (self._immediate_sessions if immediate else self._sessions)()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[Session]:
        """A session in a transaction: committed if the block succeeds, else rolled back."""
        factory = self._immediate_sessions if immediate else self._sessions
        with factory.begin() as session:
            yield session

    def begin(self, *, immediate: bool = False) -> AbstractContextManager[Connection]:
        """A Core connection in a transaction (used for schema changes)."""
        return (self._immediate_engine if immediate else self.engine).begin()

    def pragma(self, name: str) -> Any:
        """Read one SQLite setting, e.g. ``pragma("journal_mode")``."""
        if not name.replace("_", "").isalpha():
            raise ValueError(f"invalid pragma name: {name!r}")
        with self.engine.connect() as connection:
            return connection.exec_driver_sql(f"PRAGMA {name}").scalar()

    def dispose(self) -> None:
        """Close all pooled connections (the file is no longer held open)."""
        self.engine.dispose()
