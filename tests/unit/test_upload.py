"""Uploading recordings through the dashboard, in resumable pieces (T-807)."""

import gc
import hashlib
import json
import os
import re
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from reel.config import load_config
from reel.config.loader import load_groups
from reel.db import Job, JobStatus, Recording, StaffUser, Stage, init_database, open_database
from reel.ingest import upload as upload_module
from reel.ingest.upload import Lesson, UploadError, UploadStore, safe_name
from reel.web import create_app
from reel.web.security import LoginThrottle, hash_password

TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')
EXTENSIONS = (".mp4", ".mov")
MB = 1024 * 1024


def store(tmp_path, **options):
    return UploadStore(tmp_path / "data", extensions=EXTENSIONS,
                       max_file_bytes=options.pop("max_file_bytes", 100 * MB),
                       min_free_bytes=options.pop("min_free_bytes", 0))  # fmt: skip


LESSON = Lesson("Python 12", 14, None, "")


def files(*sizes):
    return [{"name": f"IMG_03{59 + i}.MOV", "size": size, "modified": 1_763_500_000_000 + i}
            for i, size in enumerate(sizes)]  # fmt: skip


def put(uploads, upload_id, index, offset, data):
    with uploads.writer(upload_id, index, offset) as write:
        write(data)


@pytest.mark.parametrize(
    ("given", "status", "text"),
    [
        ([], 400, "Ընտրեք"),
        ([{"name": "virus.exe", "size": 10}], 400, "տեսակը չի ընդունվում"),
        ([{"name": "big.mp4", "size": 101 * MB}], 413, "ԳԲ սահմանից"),
        ([{"name": "x.mp4", "size": 0}], 400, "սխալ"),
        ([{"name": "x.mp4", "size": "10"}], 400, "սխալ"),
        ([{"name": f"p{i}.mp4", "size": 1} for i in range(11)], 400, "առավելագույնը"),
    ],
)
def test_wrong_uploads_are_refused(tmp_path, given, status, text):
    with pytest.raises(UploadError) as error:
        store(tmp_path).create("anna", LESSON, given)
    assert error.value.status == status and text in str(error.value)


def test_a_full_disk_is_refused(tmp_path):
    with pytest.raises(UploadError) as error:
        store(tmp_path, min_free_bytes=10**18).create("anna", LESSON, files(10))
    assert error.value.status == 507


def test_pieces_must_continue_where_the_file_ends(tmp_path):
    uploads = store(tmp_path)
    upload_id = uploads.create("anna", LESSON, files(10))["id"]
    put(uploads, upload_id, 0, 0, b"12345")
    with pytest.raises(UploadError) as error:
        put(uploads, upload_id, 0, 0, b"again")  # already there: no overwrite
    assert error.value.status == 409
    with pytest.raises(UploadError) as error:
        put(uploads, upload_id, 0, 5, b"123456")  # more than the declared size
    assert error.value.status == 400
    with pytest.raises(UploadError) as error:
        put(uploads, upload_id, 3, 0, b"x")
    assert error.value.status == 404
    with pytest.raises(UploadError) as error:
        put(uploads, "../../etc/passwd-xxxxxxxxxxxx", 0, 0, b"x")
    assert error.value.status == 404
    assert uploads.status(upload_id)["files"][0]["received"] == 5


def test_an_interrupted_piece_keeps_what_arrived_and_resumes(tmp_path):
    uploads = store(tmp_path)
    upload_id = uploads.create("anna", LESSON, files(20))["id"]
    with pytest.raises(ConnectionError), uploads.writer(upload_id, 0, 0) as write:
        write(b"a" * 7)
        raise ConnectionError("the connection dropped")
    assert uploads.status(upload_id)["files"][0]["received"] == 7
    put(uploads, upload_id, 0, 7, b"b" * 13)
    assert uploads.status(upload_id)["files"][0]["received"] == 20


def test_a_too_big_piece_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_module, "MAX_PIECE_BYTES", 4)
    uploads = store(tmp_path)
    upload_id = uploads.create("anna", LESSON, files(20))["id"]
    with pytest.raises(UploadError) as error:
        put(uploads, upload_id, 0, 0, b"12345")
    assert error.value.status == 413


def test_safe_names():
    assert safe_name("..\\..\\Windows\\evil.mov") == "evil.mov"
    assert safe_name("../a/Դաս 14 (մաս 1).MOV") == "Դաս 14 (մաս 1).MOV"
    assert safe_name("a<b>|c?.mp4") == "a_b_c_.mp4"
    assert safe_name("...") == "recording"


@pytest.fixture
def site(project, write_config):
    write_config("[web]\nupload_max_gb = 1\n")
    write_config('[groups."Python 12"]\n', "groups.toml")
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    with db.transaction() as s:
        s.add(
            StaffUser(
                username="anna", password_hash=hash_password("upload-password-1"), role="admin"
            )
        )
    app = create_app(db, lambda: cfg, "upload-secret-" + "u" * 30, throttle=LoginThrottle())
    with TestClient(app, follow_redirects=False) as client:
        token = TOKEN.search(client.get("/login").text)[1]
        client.post("/login", data={"username": "anna", "password": "upload-password-1",
                                    "csrf_token": token})  # fmt: skip
        token = TOKEN.search(client.get("/upload").text)[1]
        yield SimpleNamespace(client=client, db=db, cfg=cfg, headers={"X-CSRF-Token": token})
    db.dispose()


def start(site, sizes, **form):
    body = {"group": "Python 12", "lesson": "14", "date": "2026-11-19", "note": "",
            "files": files(*sizes), **form}  # fmt: skip
    return site.client.post("/uploads", content=json.dumps(body), headers=site.headers)


def send(site, upload_id, index, offset, data):
    return site.client.put(f"/uploads/{upload_id}/files/{index}?offset={offset}",
                           content=data, headers=site.headers)  # fmt: skip


def test_the_upload_page_needs_login(site):
    page = site.client.get("/upload").text
    assert "Վերբեռնել ձայնագրություն" in page and '<option value="Python 12"></option>' in page
    # the group is typed (with suggestions); the lesson is plain text, because a number field
    # takes nothing from the Armenian keyboard layout
    assert '<input name="group" list="known-groups" required' in page
    assert '<input name="lesson" type="text" inputmode="numeric"' in page
    assert 'type="number"' not in page and "<select" not in page
    assert '<script src="/static/upload.js" defer>' in page
    assert site.client.get("/static/upload.js").status_code == 200
    site.client.cookies.clear()
    assert site.client.get("/upload").status_code == 303
    assert site.client.post("/uploads", content="{}").status_code == 403  # no CSRF token


def test_the_api_refuses_wrong_values(site):
    assert start(site, [10], group="   ").json()["error"] == "Գրեք խմբի անունը։"
    assert start(site, [10], group="x" * 101).status_code == 400
    assert start(site, [10], lesson="x").status_code == 400
    armenian = start(site, [10], lesson="ձ")  # the "4" key with the Armenian layout
    assert armenian.status_code == 400 and "լատինական" in armenian.json()["error"]
    assert start(site, [10], lesson="-2").status_code == 400
    assert start(site, [10], date="19.11.2026").status_code == 400
    too_big = start(site, [2 * 1024**3])
    assert too_big.status_code == 413 and "1 ԳԲ" in too_big.json()["error"]
    no_token = site.client.post("/uploads", content="{}")
    assert no_token.status_code == 403


def test_the_group_is_typed_and_matched_to_groups_toml(site):
    known = start(site, [10], group="  python   12 ", lesson=" 14 ")
    assert known.status_code == 201
    assert site.client.get(f"/uploads/{known.json()['id']}").json()["group"] == "Python 12"
    wrong = start(site, [11], group="Rust 1", lesson="x")  # a wrong form adds no group
    assert wrong.status_code == 400
    new = start(site, [11], group="Go  1")  # not in groups.toml: added to it (D-061)
    assert new.status_code == 201
    assert site.client.get(f"/uploads/{new.json()['id']}").json()["group"] == "Go 1"
    groups_file = site.cfg.paths.groups_file
    assert list(load_groups(groups_file).groups) == ["Python 12", "Go 1"]
    assert "added in the dashboard by anna on " in groups_file.read_text(encoding="utf-8")
    again = start(site, [12], group="go-1")  # the file has it now, under its first name
    assert site.client.get(f"/uploads/{again.json()['id']}").json()["group"] == "Go 1"
    assert list(load_groups(groups_file).groups) == ["Python 12", "Go 1"]


def test_a_two_part_lesson_is_uploaded_resumed_and_queued(site):
    first, second = os.urandom(3 * MB + 5), os.urandom(2 * MB)
    created = start(site, [len(first), len(second)])
    assert created.status_code == 201
    upload_id = created.json()["id"]
    assert send(site, upload_id, 0, 0, first[: 2 * MB]).json()["received"] == 2 * MB
    stale = send(site, upload_id, 0, 0, first[: 2 * MB])  # the answer was lost; sent again
    assert stale.status_code == 409 and stale.json()["received"] == 2 * MB
    assert send(site, upload_id, 0, 2 * MB, first[2 * MB :]).status_code == 200
    early = site.client.post(f"/uploads/{upload_id}/finish", headers=site.headers)
    assert early.status_code == 409 and "IMG_0360.MOV" in early.json()["error"]
    assert send(site, upload_id, 1, 0, second).json()["received"] == len(second)
    status = site.client.get(f"/uploads/{upload_id}").json()
    assert [f["received"] for f in status["files"]] == [len(first), len(second)]
    done = site.client.post(f"/uploads/{upload_id}/finish", headers=site.headers)
    assert done.status_code == 200
    job_id = done.json()["job_id"]
    assert done.json()["url"] == f"/recordings/{job_id}"
    with site.db.transaction() as s:
        job = s.get(Job, job_id)
        recording = job.recording
        assert job.status is JobStatus.QUEUED and job.stage is Stage.IMPORT
        assert (recording.group_name, recording.lesson_number) == ("Python 12", 14)
        assert str(recording.recording_date) == "2026-11-19"
        assert recording.identification["set_by_staff"] is True
        assert recording.identification["staff"]["by"] == "anna"
        parts = recording.media_info["parts"]
        assert [p["name"] for p in parts] == ["IMG_0359.MOV", "IMG_0360.MOV"]
        stored = [Path(p["path"]).read_bytes() for p in parts]
        assert stored == [first, second] and recording.size_bytes == len(first) + len(second)
        assert recording.source_path == parts[0]["path"]
        assert os.path.getmtime(parts[0]["path"]) == pytest.approx(1_763_500_000)
    assert (site.cfg.paths.data_dir / "recordings" / upload_id).is_dir()
    assert not (site.cfg.paths.data_dir / "uploads" / upload_id).exists()
    again = site.client.post(f"/uploads/{upload_id}/finish", headers=site.headers)
    assert again.status_code == 404  # finished uploads cannot be finished twice


def test_one_file_is_a_normal_recording(site):
    data = os.urandom(MB)
    upload_id = start(site, [len(data)], lesson="").json()["id"]
    send(site, upload_id, 0, 0, data)
    job_id = site.client.post(f"/uploads/{upload_id}/finish", headers=site.headers).json()["job_id"]
    with site.db.transaction() as s:
        recording = s.scalar(select(Recording))
        assert recording.media_info is None and recording.lesson_number is None
        assert s.get(Job, job_id).recording_id == recording.id


def test_an_upload_can_be_given_up(site):
    upload_id = start(site, [10]).json()["id"]
    assert site.client.delete(f"/uploads/{upload_id}", headers=site.headers).status_code == 204
    assert site.client.get(f"/uploads/{upload_id}").status_code == 404


def test_large_files_are_written_piece_by_piece_without_holding_them_in_memory(site):
    size, piece = 128 * MB, 8 * MB
    upload_id = start(site, [size]).json()["id"]
    digest = hashlib.sha256()
    tracemalloc.start()
    try:
        for offset in range(0, size, piece):
            data = bytes([offset // piece]) * piece
            digest.update(data)
            assert send(site, upload_id, 0, offset, data).status_code == 200
            del data
            gc.collect()  # the test client leaves cycles behind; the server keeps nothing
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * piece  # about one piece, not the 128 MB file
    path = site.cfg.paths.data_dir / "uploads" / upload_id / "01-IMG_0359.MOV"
    with path.open("rb") as file:
        check = hashlib.sha256()
        while block := file.read(MB):
            check.update(block)
    assert check.hexdigest() == digest.hexdigest()
