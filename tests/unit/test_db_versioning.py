"""Schema version: create, inspect, open, upgrade, back up (T-101, D-027)."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import select

from reel import __version__
from reel.db import (
    SCHEMA_VERSION,
    AppMeta,
    DatabaseError,
    Migration,
    SchemaState,
    StaffUser,
    backup_database,
    init_database,
    inspect_database,
    open_database,
    upgrade_database,
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def query(path: Path, sql: str) -> list[tuple]:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(sql).fetchall()


def execute(path: Path, sql: str) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(sql)
        connection.commit()


V = SCHEMA_VERSION  # the generic tests below migrate from it to V + 1, V + 2


def columns(path: Path, table: str) -> list[str]:
    return [row[1] for row in query(path, f"PRAGMA table_info({table})")]


@pytest.fixture
def path(tmp_path: Path) -> Path:
    path = tmp_path / "reel.db"
    init_database(path)
    return path


def test_init_creates_the_database_and_its_folder(tmp_path):
    path = tmp_path / "data" / "reel.db"
    created, info = init_database(path)
    assert created
    assert info.state is SchemaState.CURRENT
    assert info.version == SCHEMA_VERSION == 3
    assert len(info.instance_id) == 32
    assert info.journal_mode == "wal"
    assert info.created_at is not None and info.created_at.tzinfo is not None
    assert query(path, "SELECT version, app_version, description FROM schema_versions") == [
        (SCHEMA_VERSION, __version__, "initial schema")
    ]


def test_init_twice_does_nothing_and_keeps_the_instance_id(path):
    first = inspect_database(path)
    created, second = init_database(path)
    assert not created
    assert second.instance_id == first.instance_id


def test_init_accepts_an_empty_file(tmp_path):
    path = tmp_path / "reel.db"
    path.touch()
    assert inspect_database(path).state is SchemaState.EMPTY
    created, info = init_database(path)
    assert created and info.state is SchemaState.CURRENT


def test_init_refuses_a_foreign_database_and_leaves_it_untouched(tmp_path):
    path = tmp_path / "other.db"
    execute(path, "CREATE TABLE notes (x)")
    before = file_hash(path)
    assert inspect_database(path).state is SchemaState.FOREIGN
    with pytest.raises(DatabaseError, match="not a Reel database"):
        init_database(path)
    assert file_hash(path) == before


def test_a_file_that_is_not_a_database_is_unreadable(tmp_path):
    path = tmp_path / "reel.db"
    path.write_text("hello " * 200)
    info = inspect_database(path)
    assert info.state is SchemaState.UNREADABLE
    assert "not a database" in info.error
    for action in (init_database, open_database):
        with pytest.raises(DatabaseError, match="cannot be read"):
            action(path)


def test_a_folder_is_not_a_database(tmp_path):
    info = inspect_database(tmp_path)
    assert info.state is SchemaState.UNREADABLE
    assert "folder" in info.error


def test_inspecting_never_creates_or_changes_the_file(tmp_path, path):
    missing = tmp_path / "missing.db"
    assert inspect_database(missing).state is SchemaState.MISSING
    assert not missing.exists()
    before = file_hash(path)
    info = inspect_database(path, count_rows=True)
    assert info.row_counts["jobs"] == 0
    assert info.row_counts["schema_versions"] == 1
    assert info.row_counts["app_meta"] == 2
    assert file_hash(path) == before


def test_open_requires_an_initialised_database(tmp_path):
    with pytest.raises(DatabaseError, match="reel db init"):
        open_database(tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists()


def test_open_gives_a_working_database(path):
    with open_database(path) as database, database.transaction() as s:
        s.add(AppMeta(key="k", value="v"))


def test_newer_schema_is_never_touched(path):
    execute(
        path,
        "INSERT INTO schema_versions (version, applied_at, app_version, description) "
        f"VALUES ({V + 1}, '2026-11-18 00:00:00.000000', '9.0', 'from the future')",
    )
    info = inspect_database(path)
    assert info.state is SchemaState.NEWER and info.version == V + 1
    before = file_hash(path)
    for action in (open_database, init_database, upgrade_database):
        with pytest.raises(DatabaseError, match="newer than this Reel supports"):
            action(path)
    assert file_hash(path) == before


def test_outdated_schema_must_be_upgraded_before_use(path):
    assert inspect_database(path, expected_version=V + 1).state is SchemaState.OUTDATED
    with pytest.raises(DatabaseError, match="run `reel db upgrade`"):
        open_database(path, expected_version=V + 1)


MIGRATIONS_TO_3 = (  # two versions beyond the current one
    Migration(
        V + 1, "add jobs.note", lambda c: c.exec_driver_sql("ALTER TABLE jobs ADD note TEXT")
    ),
    Migration(
        V + 2, "index jobs.note", lambda c: c.exec_driver_sql("CREATE INDEX ix_n ON jobs (note)")
    ),
)


def test_upgrade_backs_up_then_applies_each_migration_once(tmp_path, path):
    result = upgrade_database(
        path, migrations=MIGRATIONS_TO_3, expected_version=V + 2, app_version="9.9"
    )
    assert (result.from_version, result.to_version) == (V, V + 2)
    assert result.backup.parent == tmp_path / "backups"
    assert f"-v{V}-" in result.backup.name
    assert inspect_database(result.backup).version == V  # the backup is the old database
    assert inspect_database(path, expected_version=V + 2).state is SchemaState.CURRENT
    assert query(path, "SELECT version, app_version, description FROM schema_versions") == [
        (V, __version__, "initial schema"),
        (V + 1, "9.9", "add jobs.note"),
        (V + 2, "9.9", "index jobs.note"),
    ]
    assert "note" in columns(path, "jobs")

    again = upgrade_database(path, migrations=MIGRATIONS_TO_3, expected_version=V + 2)
    assert (again.from_version, again.to_version, again.backup) == (V + 2, V + 2, None)


def test_a_failed_migration_leaves_the_schema_unchanged(path):
    def broken(connection):
        connection.exec_driver_sql("ALTER TABLE jobs ADD note TEXT")
        raise RuntimeError("bug in the migration")

    with pytest.raises(RuntimeError):
        upgrade_database(
            path, migrations=[Migration(V + 1, "broken", broken)], expected_version=V + 1
        )
    assert inspect_database(path).state is SchemaState.CURRENT  # still version V
    assert "note" not in columns(path, "jobs")  # the ALTER TABLE was rolled back


def test_upgrade_without_a_needed_migration_is_refused(tmp_path, path):
    with pytest.raises(DatabaseError, match="no migration to schema version"):
        upgrade_database(path, migrations=[], expected_version=V + 1)
    assert not (tmp_path / "backups").exists()


def test_upgrade_creates_tables_that_are_new_in_the_code(path):
    execute(path, "DROP TABLE library_entries")  # an older database without this table
    upgrade_database(
        path, migrations=[Migration(V + 1, "no-op", lambda c: None)], expected_version=V + 1
    )
    assert columns(path, "library_entries")


def test_backup_of_a_database_in_use(db, tmp_path):
    with db.transaction() as s:
        s.add(AppMeta(key="k", value="v"))
    first = backup_database(db.path, tmp_path / "copies", label="manual")
    second = backup_database(db.path, tmp_path / "copies", label="manual")
    assert first != second
    assert query(first, "SELECT value FROM app_meta WHERE key = 'k'") == [("v",)]


# --- version 1 -> 2 (T-1013): accounts get roles; recordings their uploader -----------------

UPLOAD = '{"staff": {"by": "anna", "source": "upload"}}'
FOLDER = '{"group": "folder"}'


def version_1(path: Path) -> None:
    """A database as Reel made it before T-1013 (the schema taken from a real one)."""
    schema = (Path(__file__).parent / "schema_v1.sql").read_text(encoding="utf-8")
    moment = "2026-11-18 18:00:00.000000"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(schema)
        connection.execute(
            "INSERT INTO schema_versions (version, applied_at, app_version, description) "
            "VALUES (1, ?, '0.1.0', 'initial schema')",
            (moment,),
        )
        connection.executemany(
            "INSERT INTO app_meta (key, value) VALUES (?, ?)",
            [
                ("instance_id", "a" * 32),
                ("created_at", "2026-11-18T18:00:00+00:00"),
            ],
        )
        connection.executemany(
            "INSERT INTO staff_users (id, username, password_hash, is_active, created_at, "
            "updated_at) VALUES (?, ?, 'h', 1, ?, ?)",
            [(1, "admin", moment, moment), (2, "anna", moment, moment)],
        )
        connection.executemany(
            "INSERT INTO recordings (id, source_path, size_bytes, identification, detected_at, "
            "created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
            [
                (1, "up.mp4", UPLOAD, moment, moment, moment),
                (2, "inbox.mp4", FOLDER, moment, moment, moment),
            ],
        )
        connection.commit()


def test_version_1_is_upgraded_to_roles_and_uploaders(tmp_path):
    path = tmp_path / "old.db"
    version_1(path)
    assert inspect_database(path).state is SchemaState.OUTDATED
    result = upgrade_database(path)
    assert (result.from_version, result.to_version) == (1, 3) and result.backup.is_file()
    # every account so far had every right: all become admins
    assert query(path, "SELECT username, role, can_publish, email FROM staff_users") == [
        ("admin", "admin", 0, None),
        ("anna", "admin", 0, None),
    ]
    # an upload is linked to the account that made it; everything made so far is published
    assert query(path, "SELECT id, uploaded_by_id, publish FROM recordings ORDER BY id") == [
        (1, 2, 1),
        (2, None, 1),
    ]
    fresh = tmp_path / "fresh.db"
    init_database(fresh)
    for table in ("staff_users", "recordings"):
        assert sorted(columns(path, table)) == sorted(columns(fresh, table))
    indexes = "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'ix_%' ORDER BY 1"
    assert query(path, indexes) == query(fresh, indexes)
    with pytest.raises(sqlite3.IntegrityError):  # an email belongs to one account
        execute(path, "UPDATE staff_users SET email = 'a@b.am'")
    with open_database(path) as database, database.transaction() as s:
        users = list(s.scalars(select(StaffUser)))
        assert [u.username for u in users] == ["admin", "anna"]
        assert all(user.is_admin and user.publishes for user in users)


def test_version_2_keeps_the_hash_of_a_video_only_processed_apart(tmp_path):
    """T-1015: a user's recording does not hold the unique (published) hash any more."""
    path = tmp_path / "old.db"
    version_1(path)
    upgrade_database(path, expected_version=2)
    published, processed = "a" * 64, "b" * 64
    execute(path, f"UPDATE recordings SET content_hash = '{published}' WHERE id = 2")
    execute(path, f"UPDATE recordings SET publish = 0, content_hash = '{processed}' WHERE id = 1")
    result = upgrade_database(path)
    assert (result.from_version, result.to_version) == (2, 3)
    assert query(path, "SELECT id, content_hash, processed_hash FROM recordings ORDER BY id") == [
        (1, None, processed),
        (2, published, None),
    ]
    fresh = tmp_path / "fresh.db"
    init_database(fresh)
    assert sorted(columns(path, "recordings")) == sorted(columns(fresh, "recordings"))
    indexes = "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'ix_%' ORDER BY 1"
    assert query(path, indexes) == query(fresh, indexes)
    # two accounts may have the same file: the processed hash is not unique
    execute(path, f"UPDATE recordings SET processed_hash = '{processed}'")
