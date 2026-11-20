"""Connections and transactions (T-101, D-027): WAL, foreign keys, locks, rollback."""

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from reel.db import AppMeta, Database, database_url, init_database, open_database


def keys(db: Database) -> set[str]:
    with db.transaction() as s:
        return set(s.scalars(select(AppMeta.key)))


def test_connection_settings(db):
    assert db.pragma("journal_mode") == "wal"
    assert db.pragma("foreign_keys") == 1
    assert db.pragma("busy_timeout") == 30_000
    assert db.pragma("synchronous") == 2  # FULL


def test_wal_mode_is_stored_in_the_file_and_used(db):
    with db.transaction() as s:
        s.add(AppMeta(key="k", value="v"))
    assert Path(f"{db.path}-wal").exists()
    with closing(sqlite3.connect(db.path)) as plain:  # a connection without Reel's settings
        assert plain.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_invalid_pragma_name_is_rejected(db):
    with pytest.raises(ValueError):
        db.pragma("journal_mode; DROP TABLE jobs")


def test_opening_a_missing_file_fails_and_creates_nothing(tmp_path):
    path = tmp_path / "missing.db"
    database = Database(path)
    try:
        with pytest.raises(OperationalError):
            database.pragma("journal_mode")
    finally:
        database.dispose()
    assert not path.exists()


def test_url_is_a_sqlite_uri(tmp_path):
    url = database_url(tmp_path / "x.db")
    assert url.database.startswith("file:///")
    assert dict(url.query) == {"uri": "true", "mode": "rw"}
    assert database_url(tmp_path / "x.db", create=True).query["mode"] == "rwc"


def test_folder_with_spaces_and_armenian_letters(tmp_path):
    path = tmp_path / "Ռիլ տվյալներ" / "reel.db"
    init_database(path)
    with open_database(path) as database, database.transaction() as s:
        s.add(AppMeta(key="Դաս", value="14"))
    with open_database(path) as database:
        assert "Դաս" in keys(database)


def test_transaction_commits_on_success_and_rolls_back_on_error(db):
    with db.transaction() as s:
        s.add(AppMeta(key="kept", value="1"))
    with pytest.raises(RuntimeError), db.transaction() as s:
        s.add(AppMeta(key="lost", value="1"))
        s.flush()
        raise RuntimeError("boom")
    assert "kept" in keys(db) and "lost" not in keys(db)


def test_savepoint_rolls_back_only_its_own_part(db):
    with db.transaction() as s:
        s.add(AppMeta(key="outer", value="1"))
        with pytest.raises(ValueError), s.begin_nested():
            s.add(AppMeta(key="inner", value="1"))
            s.flush()
            raise ValueError
    assert "outer" in keys(db) and "inner" not in keys(db)


def test_objects_stay_readable_after_the_transaction(db):
    with db.transaction() as s:
        meta = AppMeta(key="k", value="v")
        s.add(meta)
    assert meta.value == "v"


def test_readers_are_not_blocked_by_an_open_write_transaction(db):
    with db.transaction() as s:
        s.add(AppMeta(key="k", value="old"))
    with db.transaction(immediate=True) as writer:
        writer.get(AppMeta, "k").value = "new"
        writer.flush()  # written, not committed
        started = time.monotonic()
        with db.transaction() as reader:
            assert reader.get(AppMeta, "k").value == "old"
        assert time.monotonic() - started < 5
    with db.transaction() as s:
        assert s.get(AppMeta, "k").value == "new"


def test_deferred_read_then_write_fails_after_a_concurrent_commit(db):
    """Why queue updates use ``immediate=True``: a plain transaction that read old data
    cannot write after another connection committed (SQLite refuses at once)."""
    with db.transaction() as s:
        s.add(AppMeta(key="k", value="0"))
    with Database(db.path, busy_timeout=1) as quick:
        stale = quick.session()
        try:
            row = stale.get(AppMeta, "k")  # BEGIN (deferred) + read
            with quick.transaction() as other:
                other.get(AppMeta, "k").value = "1"
            row.value = "2"
            with pytest.raises(OperationalError, match="locked"):
                stale.commit()
        finally:
            stale.close()
    with db.transaction() as s:
        assert s.get(AppMeta, "k").value == "1"


def test_immediate_transactions_serialise_concurrent_writers(db):
    """Read-modify-write from several threads: no lost update, no 'database is locked'."""
    with db.transaction() as s:
        s.add(AppMeta(key="counter", value="0"))
    errors: list[Exception] = []

    def work() -> None:
        try:
            for _ in range(25):
                with db.transaction(immediate=True) as s:
                    row = s.get(AppMeta, "counter")
                    row.value = str(int(row.value) + 1)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with db.transaction() as s:
        assert s.get(AppMeta, "counter").value == "100"


def test_dispose_releases_the_file(tmp_path):
    path = tmp_path / "reel.db"
    init_database(path)
    database = open_database(path)
    with database.transaction() as s:
        s.add(AppMeta(key="k", value="v"))
    database.dispose()
    moved = path.rename(tmp_path / "moved.db")  # fails on Windows while the file is open
    assert moved.exists()
