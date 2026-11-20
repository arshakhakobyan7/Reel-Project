"""Library port on fixture databases and loopback-only HTTP mocks (T-705)."""

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from reel.config import load_config
from reel.db import AppMeta, Job, LibraryEntry, Recording, init_database, open_database
from reel.library import (
    Chapter,
    LibraryItem,
    PermanentLibraryError,
    RetryableLibraryError,
    local,
    make_library_publisher,
)
from reel.library.http import HttpLibraryPublisher
from reel.library.local import LocalLibraryPublisher, upsert_library_entry
from reel.library.mock_server import LibraryMockServer
from reel.library.port import MAX_JSON_BYTES, external_key

TOKEN = "synthetic-library-test-token"
T0 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def item():
    return LibraryItem(
        title="Python 12 · Դաս 7",
        video_url="https://youtu.be/synthetic7",
        group="Python 12",
        lesson_number=7,
        recording_date=date(2026, 11, 19),
        chapters=[
            {"start_seconds": 0.0, "title": "Ներածություն"},
            {"start_seconds": 93.5, "title": "Բառարաններ և օրինակներ"},
        ],
    )


def add_job(session):
    recording = Recording(source_path="synthetic-lesson.mp4", size_bytes=1)
    job = Job(recording=recording)
    session.add(job)
    session.flush()
    return job


@pytest.fixture
def job_id(db):
    with db.transaction() as session:
        return add_job(session).id


def instance(db):
    with db.transaction() as session:
        return session.get(AppMeta, "instance_id").value


def count(db):
    with db.transaction() as session:
        return session.scalar(select(func.count()).select_from(LibraryEntry))


def remote(server, db, **kwargs):
    return HttpLibraryPublisher(server.url, instance_id=instance(db), token=TOKEN, **kwargs)


def test_local_upsert_preserves_row_id_and_creation_time_and_moves_updated_at(
    db, job_id, item, monkeypatch
):
    monkeypatch.setattr(local, "utcnow", lambda: T0)
    first = LocalLibraryPublisher(db).publish(job_id, item)
    assert first.created and not first.updated and first.url is None
    with db.transaction() as session:
        row = session.get(LibraryEntry, int(first.entry_id))
        assert row.title == item.title and row.video_url == item.video_url
        assert row.group_name == item.group and row.lesson_number == item.lesson_number
        assert (
            row.recording_date == item.recording_date and row.chapters == item.to_dict()["chapters"]
        )
        assert row.created_at == row.updated_at == T0
    changed = replace(
        item,
        title="Թարմացված դաս",
        video_url="https://youtu.be/changed",
        group="Դիզայն 3",
        lesson_number=None,
        recording_date=None,
        chapters=[Chapter(0, "Նոր սկիզբ")],
    )
    second = LocalLibraryPublisher(db).publish(job_id, changed)
    assert second.entry_id == first.entry_id and second.updated and not second.created
    assert count(db) == 1
    with db.transaction() as session:
        row = session.get(LibraryEntry, int(first.entry_id))
        assert row.created_at == T0 and row.updated_at > T0 and row.updated_at.tzinfo == UTC
        assert (row.title, row.video_url, row.group_name) == (
            changed.title,
            changed.video_url,
            changed.group,
        )
        assert row.lesson_number is row.recording_date is None
        assert row.chapters == changed.to_dict()["chapters"]


def test_session_upsert_is_part_of_outer_commit_and_refreshes_cached_entry(db, item):
    with db.transaction(immediate=True) as session:
        job = add_job(session)
        first = upsert_library_entry(session, job.id, item)
        cached = session.get(LibraryEntry, int(first.entry_id))
        second = upsert_library_entry(session, job.id, replace(item, title="Նոր վերնագիր"))
        job.flags = ["stage-owned-change"]
        assert session.in_transaction() and first.entry_id == second.entry_id
        assert cached.title == "Նոր վերնագիր"
        assert count(db) == 0  # Another connection cannot see the uncommitted entry.
        saved_job_id = job.id
    assert count(db) == 1
    with db.transaction() as session:
        assert session.get(Job, saved_job_id).flags == ["stage-owned-change"]


def test_session_upsert_rolls_back_with_outer_stage_transaction(db, item):
    with (
        pytest.raises(RuntimeError, match="stage failed"),
        db.transaction(immediate=True) as session,
    ):
        job = add_job(session)
        upsert_library_entry(session, job.id, item)
        assert count(db) == 0
        raise RuntimeError("stage failed")
    assert count(db) == 0
    with db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_competing_local_publishes_create_exactly_one_row(db, job_id, item):
    barrier = threading.Barrier(2)

    def publish():
        barrier.wait(timeout=5)
        return LocalLibraryPublisher(db).publish(job_id, item)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0].entry_id == results[1].entry_id
    assert sorted(result.created for result in results) == [False, True]
    assert count(db) == 1


def test_different_local_jobs_have_different_entries(db, job_id, item):
    with db.transaction() as session:
        other_id = add_job(session).id
    first = LocalLibraryPublisher(db).publish(job_id, item)
    second = LocalLibraryPublisher(db).publish(other_id, item)
    assert first.entry_id != second.entry_id and count(db) == 2


def test_missing_local_job_is_a_sanitised_permanent_failure(db, item):
    with pytest.raises(PermanentLibraryError, match="missing_job"):
        LocalLibraryPublisher(db).publish(99999, item)
    assert count(db) == 0


def test_item_roundtrip_is_utf8_and_chapter_input_is_defensively_copied(item):
    raw = json.dumps(item.to_dict(), ensure_ascii=False).encode()
    assert "Դաս".encode() in raw
    assert LibraryItem.from_dict(json.loads(raw)).to_dict() == item.to_dict()
    chapters = [{"start_seconds": 0, "title": "Սկիզբ"}]
    copy = replace(item, chapters=chapters)
    chapters[0]["title"] = "mutated"
    assert copy.to_dict()["chapters"][0]["title"] == "Սկիզբ"
    with pytest.raises(FrozenInstanceError):
        copy.title = "mutated"
    output = copy.to_dict()
    output["chapters"][0]["title"] = "another mutation"
    assert copy.to_dict()["chapters"][0]["title"] == "Սկիզբ"


@pytest.mark.parametrize(
    "patch",
    [
        {"title": ""},
        {"group": 7},
        {"video_url": "javascript:alert(1)"},
        {"lesson_number": True},
        {"lesson_number": -1},
        {"recording_date": "20261119"},
        {"recording_date": 123},
        {"chapters": "none"},
        {"chapters": [{"start": 0, "title": "wrong field"}]},
        {"chapters": [{"start_seconds": -1, "title": "bad"}]},
        {"chapters": [{"start_seconds": True, "title": "bad"}]},
        {"chapters": [{"start_seconds": float("nan"), "title": "bad"}]},
        {"chapters": [{"start_seconds": 10**400, "title": "bad"}]},
        {"chapters": [{"start_seconds": 0, "title": "a"}, {"start_seconds": 0, "title": "b"}]},
        {"extra": "unsupported"},
    ],
)
def test_item_validation_rejects_invalid_contract_fields(item, patch):
    with pytest.raises(ValueError):
        LibraryItem.from_dict({**item.to_dict(), **patch})


@pytest.mark.parametrize("job_id", [0, -1, True, "1"])
def test_external_key_rejects_invalid_job_ids(db, job_id):
    with pytest.raises(PermanentLibraryError, match="invalid_job_id"):
        external_key(instance(db), job_id)


def test_http_create_then_update_preserves_identity_and_all_fields_in_armenian(db, item):
    with LibraryMockServer(token=TOKEN) as server:
        first = remote(server, db).publish(7, item)
        changed = replace(item, title="Թարմացված վերնագիր", chapters=[Chapter(0, "Ներածություն")])
        second = remote(server, db).publish(7, changed)
        assert first.created and second.updated and first.entry_id == second.entry_id
        assert first.external_key == second.external_key == external_key(instance(db), 7)
        assert first.url == second.url and first.url.startswith(server.url)
        assert len(server.entries) == 1
        assert server.entries[first.external_key]["item"] == changed.to_dict()
        assert server.requests[0]["item"] == item.to_dict()
        assert "Դաս".encode() in server.bodies[0]
        assert TOKEN not in repr(server.requests) + repr(server.entries)


def test_lost_response_after_committed_put_is_idempotent(db, item):
    with LibraryMockServer(token=TOKEN, drop_after_commit_once=True) as server:
        with pytest.raises(RetryableLibraryError):
            remote(server, db).publish(7, item)
        assert len(server.entries) == 1
        result = remote(server, db).publish(7, item)
        assert result.updated and result.entry_id == "entry-1" and len(server.entries) == 1
        assert server.requests[0]["external_key"] == server.requests[1]["external_key"]


def test_keys_survive_factory_recreation_and_differ_across_databases(
    db, item, tmp_path, project, write_config
):
    other_path = tmp_path / "other.db"
    init_database(other_path)
    other = open_database(other_path)
    try:
        with LibraryMockServer(token=TOKEN) as server:
            write_config(f'[library]\nprovider="http"\nhttp_base_url="{server.url}"\n')
            cfg = load_config(
                project / "config" / "reel.toml", environ={"LIBRARY_API_TOKEN": TOKEN}
            )
            first = make_library_publisher(cfg, db).publish(7, item)
            second = make_library_publisher(cfg, db).publish(7, item)
            third = make_library_publisher(cfg, other).publish(7, item)
            assert first.external_key == second.external_key != third.external_key
            assert second.updated and third.created and len(server.entries) == 2
            assert isinstance(make_library_publisher(cfg, db), HttpLibraryPublisher)
    finally:
        other.dispose()


@pytest.mark.parametrize("status", [408, 429, 500, 503, 400, 401, 403, 404, 422])
def test_http_statuses_are_distinct_retryable_or_permanent_errors(db, item, status, caplog, capsys):
    expected = (
        RetryableLibraryError if status in {408, 429} or status >= 500 else PermanentLibraryError
    )
    with LibraryMockServer(
        token=TOKEN, status=status, raw_body=TOKEN.encode(), response_headers={"Retry-After": "2"}
    ) as server:
        with pytest.raises(expected) as error:
            remote(server, db).publish(7, item)
        assert not server.entries
    assert error.value.retryable == (expected is RetryableLibraryError)
    assert error.value.retry_after == 2 and error.value.code == f"http_{status}"
    output = capsys.readouterr()
    assert (
        TOKEN
        not in "".join(traceback.format_exception(error.value))
        + caplog.text
        + output.out
        + output.err
    )


def test_http_timeout_comes_from_factory_settings_and_is_fast(db, item, project, write_config):
    with LibraryMockServer(token=TOKEN, delay_seconds=0.2) as server:
        write_config(
            f'[library]\nprovider="http"\nhttp_base_url="{server.url}"\ntimeout_seconds=0.02\n'
        )
        cfg = load_config(project / "config" / "reel.toml", environ={"LIBRARY_API_TOKEN": TOKEN})
        started = time.monotonic()
        with pytest.raises(RetryableLibraryError, match="timeout"):
            make_library_publisher(cfg, db).publish(7, item)
        assert time.monotonic() - started < 1


def test_http_connection_failure_is_retryable(db, item):
    with LibraryMockServer() as server:
        url = server.url
    publisher = HttpLibraryPublisher(
        url, instance_id=instance(db), token=TOKEN, timeout_seconds=0.02
    )
    with pytest.raises(RetryableLibraryError):
        publisher.publish(7, item)


@pytest.mark.parametrize("raw", [b"{", b"\xff", b'{"id":NaN}', b'{"id":1,"id":2}', b"[]"])
def test_http_malformed_json_is_retryable_without_fake_success(db, item, raw):
    with (
        LibraryMockServer(token=TOKEN, raw_body=raw) as server,
        pytest.raises(RetryableLibraryError, match="invalid_response"),
    ):
        remote(server, db).publish(7, item)


@pytest.mark.parametrize(
    "patch",
    [
        {"external_key": "wrong"},
        {"id": 1},
        {"id": ""},
        {"id": "bad\nid"},
        {"url": "javascript:alert(1)"},
        {"created": "true"},
        {"created": False},
        {"item": {}},
        {"extra": "not allowed"},
    ],
)
def test_http_strict_response_validation_checks_shape_and_identity(db, item, patch):
    payload = {
        "external_key": external_key(instance(db), 7),
        "id": "entry-1",
        "url": "https://library.example/entries/1",
        "created": True,
        "item": item.to_dict(),
    }
    with (
        LibraryMockServer(
            token=TOKEN, raw_body=json.dumps({**payload, **patch}).encode()
        ) as server,
        pytest.raises(RetryableLibraryError, match="invalid_response"),
    ):
        remote(server, db).publish(7, item)


def test_http_rejects_mismatched_echoed_metadata(db, item):
    payload = {
        "external_key": external_key(instance(db), 7),
        "id": "entry-1",
        "url": "https://library.example/entries/1",
        "created": True,
        "item": replace(item, title="Wrong lesson").to_dict(),
    }
    with (
        LibraryMockServer(token=TOKEN, raw_body=json.dumps(payload).encode()) as server,
        pytest.raises(RetryableLibraryError),
    ):
        remote(server, db).publish(7, item)


@pytest.mark.parametrize("content_type", ["text/html", "application/json; charset=latin-1"])
def test_http_rejects_non_utf8_json_content_type(db, item, content_type):
    with (
        LibraryMockServer(token=TOKEN, content_type=content_type) as server,
        pytest.raises(RetryableLibraryError),
    ):
        remote(server, db).publish(7, item)


def test_http_rejects_oversized_response(db, item):
    with (
        LibraryMockServer(token=TOKEN, raw_body=b" " * (MAX_JSON_BYTES + 1)) as server,
        pytest.raises(RetryableLibraryError),
    ):
        remote(server, db).publish(7, item)


def test_http_redirect_does_not_forward_credentials(db, item):
    with LibraryMockServer(token=TOKEN) as target:
        with (
            LibraryMockServer(
                token=TOKEN, status=307, response_headers={"Location": target.url}
            ) as source,
            pytest.raises(PermanentLibraryError, match="http_307"),
        ):
            remote(source, db).publish(7, item)
        assert target.request_count == 0


@pytest.mark.parametrize("token", [None, "", "bad token", "bad\r\nheader", "ոչ-ascii"])
def test_missing_or_invalid_token_is_permanent_without_http(db, item, token):
    with LibraryMockServer(token=TOKEN) as server:
        publisher = HttpLibraryPublisher(server.url, instance_id=instance(db), token=token)
        with pytest.raises(PermanentLibraryError, match="LIBRARY_API_TOKEN"):
            publisher.publish(7, item)
        assert server.request_count == 0


def test_wrong_token_is_permanent_auth_error(db, item):
    with LibraryMockServer(token=TOKEN) as server:
        publisher = HttpLibraryPublisher(
            server.url, instance_id=instance(db), token="wrong-fake-token"
        )
        with pytest.raises(PermanentLibraryError, match="http_401"):
            publisher.publish(7, item)


def test_factory_chooses_local_and_can_preview_without_database_writes(
    db, job_id, item, project, write_config
):
    write_config('[library]\nprovider="local"\n')
    cfg = load_config(project / "config" / "reel.toml", environ={})
    preview = make_library_publisher(cfg, db, dry_run=True).publish(job_id, item)
    assert preview.dry_run and preview.entry_id is preview.url is None
    assert not preview.created and not preview.updated and preview.payload == item.to_dict()
    assert count(db) == 0
    publisher = make_library_publisher(cfg, db)
    assert isinstance(publisher, LocalLibraryPublisher) and publisher.name == "local"
    assert publisher.publish(job_id, item).created and count(db) == 1


def test_factory_http_dry_run_needs_no_token_and_sends_no_requests(db, item, project, write_config):
    with LibraryMockServer(token=TOKEN) as server:
        write_config(f'[library]\nprovider="http"\nhttp_base_url="{server.url}"\n')
        cfg = load_config(project / "config" / "reel.toml", environ={})
        result = make_library_publisher(cfg, db, dry_run=True).publish(7, item)
        assert result.dry_run and result.entry_id is result.url is None
        assert not result.created and not result.updated and result.payload == item.to_dict()
        assert result.external_key == external_key(instance(db), 7) and server.request_count == 0


def test_missing_database_instance_is_not_silently_regenerated(db, project, write_config):
    write_config('[library]\nprovider="http"\nhttp_base_url="http://127.0.0.1"\n')
    cfg = load_config(project / "config" / "reel.toml", environ={"LIBRARY_API_TOKEN": TOKEN})
    with db.transaction() as session:
        session.delete(session.get(AppMeta, "instance_id"))
    with pytest.raises(PermanentLibraryError, match="missing_instance_id"):
        make_library_publisher(cfg, db)


@pytest.mark.parametrize("value", ["", "not-an-instance", None])
def test_invalid_instance_ids_are_rejected(value):
    with pytest.raises(PermanentLibraryError, match="invalid_instance_id"):
        external_key(value, 1)
