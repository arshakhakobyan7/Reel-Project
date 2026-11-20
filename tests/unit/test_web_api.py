"""The read-only integration API (T-806): tokens, schema, filters, paging, privacy."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_web_dashboard import add_job

from reel.config import load_config
from reel.db import (
    ConsentCheck,
    Job,
    JobStatus,
    LibraryEntry,
    PublicationTarget,
    Stage,
    Transcript,
    VideoMetadata,
    init_database,
    open_database,
)
from reel.db.enums import ConsentResult, PublicationStatus, TargetKind, TranscriptKind
from reel.web import create_app

TOKEN = "integration-token-" + "t" * 20
ITEM_KEYS = {
    "id", "title", "group", "lesson_number", "recording_date", "status", "stage",
    "original_duration_seconds", "final_duration_seconds", "youtube_url", "flags",
    "created_at", "updated_at", "finished_at",
}  # fmt: skip
DETAIL_KEYS = ITEM_KEYS | {
    "summary", "description", "chapters", "removed_seconds", "consent", "publications",
}  # fmt: skip
LIBRARY_KEYS = {
    "id", "recording_id", "title", "video_url", "group", "lesson_number", "recording_date",
    "chapters", "updated_at",
}  # fmt: skip


def site(project, write_config, tokens):
    if tokens is not None:
        (project / ".env").write_text(f"REEL_API_TOKENS={tokens}\n", encoding="utf-8")
    write_config("")
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    return db, TestClient(create_app(db, lambda: cfg, "api-secret-" + "a" * 30))


@pytest.fixture
def api(project, write_config):
    db, client = site(project, write_config, f" short ,{TOKEN}")
    with client:
        yield db, client
    db.dispose()


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def published(db):
    job_id = add_job(db, group="Python 12", lesson=14, status=JobStatus.DONE, stage=Stage.POST,
                     original=9672.0, final=6723.0)  # fmt: skip
    with db.transaction() as s:
        s.get(Job, job_id).recording.recording_date = date(2026, 11, 19)
        s.add(VideoMetadata(job_id=job_id, title="Python 12 · Դաս 14", summary="Բառարաններ",
                            description="Նկարագրություն",
                            chapters=[{"start": 0.0, "title": "Կրկնություն"}]))  # fmt: skip
        s.add(Transcript(job_id=job_id, kind=TranscriptKind.CLEAN, language="hy",
                         text="գաղտնի տառադարձում", segments=[]))  # fmt: skip
        s.add(ConsentCheck(job_id=job_id, provider="file", result=ConsentResult.PASSED,
                           students=[{"id": "student-7", "status": "granted"}]))  # fmt: skip
        s.add(PublicationTarget(job_id=job_id, target=TargetKind.YOUTUBE,
                                status=PublicationStatus.PUBLISHED,
                                url="https://www.youtube.com/watch?v=abc",
                                published_at=datetime(2026, 11, 19, 17, tzinfo=UTC)))  # fmt: skip
        s.add(PublicationTarget(job_id=job_id, target=TargetKind.SLACK, dry_run=True,
                                status=PublicationStatus.PUBLISHED, url=None))  # fmt: skip
        s.add(LibraryEntry(job_id=job_id, title="Python 12 · Դաս 14",
                           video_url="https://www.youtube.com/watch?v=abc", group_name="Python 12",
                           lesson_number=14, recording_date=date(2026, 11, 19),
                           chapters=[{"start_seconds": 0.0, "title": "Կրկնություն"}]))  # fmt: skip
    return job_id


def test_a_valid_token_is_required(api):
    _, client = api
    wrong = [
        {},
        {"Authorization": "Bearer wrong-token-xxxxxxxxxxxxxxxx"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": "Bearer short"},  # configured, but too short to be used
    ]
    for headers in wrong:
        response = client.get("/api/v1/recordings", headers=headers)
        assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
        assert response.json() == {"error": "a valid bearer token is required"}
    ok = client.get("/api/v1/recordings", headers=AUTH)
    assert ok.status_code == 200 and "set-cookie" not in ok.headers  # no dashboard session
    assert ok.headers["cache-control"] == "no-store"


def test_without_tokens_the_api_is_off(project, write_config):
    db, client = site(project, write_config, None)
    with db, client:
        response = client.get("/api/v1/library", headers=AUTH)
        assert response.status_code == 503 and "REEL_API_TOKENS" in response.json()["error"]


def test_the_recording_list_and_detail_match_the_documented_schema(api):
    db, client = api
    job_id = published(db)
    items = client.get("/api/v1/recordings", headers=AUTH).json()["items"]
    assert len(items) == 1 and set(items[0]) == ITEM_KEYS
    item = items[0]
    assert item["id"] == job_id and item["title"] == "Python 12 · Դաս 14"
    assert (item["status"], item["stage"], item["lesson_number"]) == ("done", "post", 14)
    assert item["final_duration_seconds"] == 6723.0 and item["recording_date"] == "2026-11-19"
    assert item["youtube_url"] == "https://www.youtube.com/watch?v=abc"
    detail = client.get(f"/api/v1/recordings/{job_id}", headers=AUTH).json()
    assert set(detail) == DETAIL_KEYS
    assert detail["chapters"] == [{"start_seconds": 0.0, "title": "Կրկնություն"}]
    assert detail["consent"]["result"] == "passed" and detail["removed_seconds"] == 60.0
    assert detail["publications"]["slack"] == {"status": "published", "dry_run": True,
                                               "url": None, "published_at": None}  # fmt: skip
    assert detail["publications"]["youtube"]["published_at"].startswith("2026-11-19T17")
    text = str(detail)
    assert "գաղտնի տառադարձում" not in text and "student-7" not in text  # private
    missing = client.get("/api/v1/recordings/999", headers=AUTH)
    assert missing.status_code == 404 and missing.json() == {"error": "no such recording"}


def test_filters_and_paging(api):
    db, client = api
    ids = [
        add_job(db, group="Python 12" if i % 2 else "Java 3", lesson=i,
                status=JobStatus.DONE if i % 3 == 0 else JobStatus.QUEUED)
        for i in range(7)
    ]  # fmt: skip
    page = client.get("/api/v1/recordings?limit=3", headers=AUTH).json()
    assert [i["id"] for i in page["items"]] == ids[:3] and page["next_after"] == ids[2]
    rest = client.get(f"/api/v1/recordings?limit=3&after={ids[2]}", headers=AUTH).json()
    assert [i["id"] for i in rest["items"]] == ids[3:6]
    last = client.get(f"/api/v1/recordings?after={ids[5]}", headers=AUTH).json()
    assert [i["id"] for i in last["items"]] == ids[6:] and last["next_after"] is None
    done = client.get("/api/v1/recordings?status=done", headers=AUTH).json()["items"]
    assert {i["id"] for i in done} == {ids[0], ids[3], ids[6]}
    java = client.get("/api/v1/recordings?group=Java+3", headers=AUTH).json()["items"]
    assert {i["group"] for i in java} == {"Java 3"} and len(java) == 4
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    later = client.get("/api/v1/recordings", params={"updated_since": future}, headers=AUTH)
    assert later.json()["items"] == []
    for bad in ("status=nope", "limit=x", "after=x", "updated_since=2026-11-19T10:00"):
        assert client.get(f"/api/v1/recordings?{bad}", headers=AUTH).status_code == 400


def test_the_library(api):
    db, client = api
    job_id = published(db)
    items = client.get("/api/v1/library", headers=AUTH).json()["items"]
    assert len(items) == 1 and set(items[0]) == LIBRARY_KEYS
    assert items[0]["recording_id"] == job_id and items[0]["chapters"][0]["title"] == "Կրկնություն"
    assert client.get("/api/v1/library?group=Java+3", headers=AUTH).json()["items"] == []


def test_unknown_api_paths_answer_json(api):
    _, client = api
    response = client.get("/api/v1/nothing", headers=AUTH)
    assert response.status_code == 404 and response.json() == {"error": "not found"}
    assert client.post("/api/v1/recordings", headers=AUTH).status_code in {403, 405}
