"""The recording section wording changes without renaming the chapters stage (T-1017)."""

import re
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_web_dashboard import add_job

from reel.config import load_config
from reel.db import JobStatus, StaffUser, Stage, VideoMetadata, init_database, open_database
from reel.web import create_app
from reel.web.security import LoginThrottle, hash_password

TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')
CHAPTERS = [
    {"start": 0.0, "title": "Կրկնություն"},
    {"start": 1120.0, "title": "Բառարաններ"},
]


@pytest.fixture
def site(project: Path, write_config: Callable[..., Path]) -> Iterator[SimpleNamespace]:
    """Serve an authenticated recording page using temporary configuration and records."""
    write_config("")
    cfg = load_config(project / "config" / "reel.toml", environ={})
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    password = secrets.token_urlsafe(18)
    try:
        with db.transaction() as session:
            session.add(
                StaffUser(
                    username="heading-admin", password_hash=hash_password(password), role="admin"
                )
            )
        app = create_app(db, lambda: cfg, secrets.token_urlsafe(40), throttle=LoginThrottle())
        with TestClient(app, follow_redirects=False) as client:
            token = TOKEN.search(client.get("/login").text)[1]
            response = client.post(
                "/login",
                data={"username": "heading-admin", "password": password, "csrf_token": token},
            )
            assert response.status_code == 303
            yield SimpleNamespace(client=client, db=db)
    finally:
        db.dispose()


def add_recording(site: SimpleNamespace, chapters: list[dict[str, float | str]]) -> int:
    """Add a recording with metadata so the chapters section is rendered."""
    job_id = add_job(
        site.db,
        group="Python 12",
        lesson=14,
        status=JobStatus.DONE,
        stage=Stage.POST,
        original=1200.0,
    )
    with site.db.transaction() as session:
        session.add(VideoMetadata(job_id=job_id, title="Python 12 · Դաս 14", chapters=chapters))
    return job_id


def test_recording_with_chapters_has_the_renamed_heading_and_list(site):
    job_id = add_recording(site, CHAPTERS)
    response = site.client.get(f"/recordings/{job_id}")
    assert response.status_code == 200
    assert "<h2>Հատվածավորում</h2>" in response.text
    assert "<h2>Գլուխներ</h2>" not in response.text
    assert (
        '<ol class="chapters"><li><span class="at">0:00</span> Կրկնություն</li>'
        '<li><span class="at">18:40</span> Բառարաններ</li></ol>'
    ) in response.text


def test_recording_without_chapters_has_the_renamed_empty_text(site):
    job_id = add_recording(site, [])
    response = site.client.get(f"/recordings/{job_id}")
    assert response.status_code == 200
    assert "<h2>Հատվածավորում</h2>" in response.text
    assert '<p class="muted">Հատվածներ չկան։</p>' in response.text
    assert "Գլուխներ չկան։" not in response.text
    assert '<ol class="chapters">' not in response.text


def test_chapters_stage_name_is_unchanged_in_pills(site):
    job_id = add_recording(site, CHAPTERS)
    response = site.client.get(f"/recordings/{job_id}")
    assert response.status_code == 200
    pills = re.search(r'<ol class="pills big">(.*?)</ol>', response.text, re.DOTALL)
    assert pills is not None
    assert re.search(r'<li class="pill [^"]+" title="[^"]*">Գլուխներ</li>', pills[1])
    assert "Հատվածավորում" not in pills[1]
