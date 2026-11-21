"""Accounts (T-1013, D-065): registration, what a user sees and may do, and the admin's
account pages."""

import json
import re
from datetime import timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from test_web_dashboard import add_job

from reel.config import load_config
from reel.db import (
    ROLE_ADMIN,
    Job,
    JobStatus,
    Recording,
    StaffUser,
    Stage,
    init_database,
    open_database,
    utcnow,
)
from reel.retention import cleanup
from reel.web import create_app, texts
from reel.web.forms import parse_registration
from reel.web.security import LoginThrottle, hash_password
from reel.web.views import local_text

SECRET = "accounts-test-secret-" + "a" * 30
TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')
ADMIN_PASSWORD = "admin-password-1"
FORM = {
    "first_name": "Աննա",
    "last_name": "Պետրոսյան",
    "email": "Anna.P@Example.com",
    "phone": "+374 91 123456",
    "password": "anna-password-1",
    "password_again": "anna-password-1",
}


@pytest.fixture
def site(project, write_config):
    write_config("[web]\nupload_max_gb = 1\n")
    write_config('[groups."Python 12"]\n', "groups.toml")
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    with db.transaction() as s:
        s.add(StaffUser(username="admin", password_hash=hash_password(ADMIN_PASSWORD),
                        role=ROLE_ADMIN, display_name="Admin"))  # fmt: skip
    app = create_app(db, lambda: cfg, SECRET, throttle=LoginThrottle())
    clients = []

    def client() -> TestClient:
        made = TestClient(app, follow_redirects=False)
        made.__enter__()
        clients.append(made)
        return made

    yield SimpleNamespace(db=db, cfg=cfg, app=app, client=client, project=project)
    for made in clients:
        made.__exit__(None, None, None)
    db.dispose()


def token(client, path="/login"):
    return TOKEN.search(client.get(path).text)[1]


def login(site, username, password):
    client = site.client()
    data = {"username": username, "password": password, "csrf_token": token(client)}
    assert client.post("/login", data=data).status_code == 303
    return client


def register(site, client=None, **changes):
    client = client or site.client()
    data = {**FORM, **changes, "csrf_token": token(client, "/register")}
    return client, client.post("/register", data=data)


def account(db, username):
    with db.transaction() as s:
        user = s.query(StaffUser).filter_by(username=username).one()
        s.expunge(user)
        return user


def own_job(db, owner_id, **fields):
    fields = {"status": JobStatus.DONE, "stage": Stage.POST, **fields}
    job_id = add_job(db, group="Python 12", lesson=1, **fields)
    with db.transaction() as s:
        recording = s.get(Job, job_id).recording
        recording.uploaded_by_id, recording.publish = owner_id, False
    return job_id


# --- registration --------------------------------------------------------------------------


def test_a_new_user_registers_and_is_signed_in_at_once(site):
    client = site.client()
    assert 'href="/register"' in client.get("/login").text
    _, response = register(site, client)
    assert (response.status_code, response.headers["location"]) == (303, "/")
    home = client.get("/").text
    assert "Աննա Պետրոսյան" in home and 'href="/upload"' in home
    assert 'href="/metrics"' not in home and 'href="/admin/users"' not in home
    user = account(site.db, "anna.p@example.com")
    assert (user.first_name, user.last_name, user.email, user.phone) == (
        "Աննա", "Պետրոսյան", "anna.p@example.com", "+374 91 123456"
    )  # fmt: skip
    assert (user.role, user.can_publish, user.is_admin, user.publishes) == (
        "user", False, False, False
    )  # fmt: skip
    # later the email is the login
    again = login(site, "ANNA.P@example.com", FORM["password"])
    assert "Աննա Պետրոսյան" in again.get("/").text


def test_the_phone_number_is_optional(site):
    _, response = register(site, phone="")
    assert response.status_code == 303
    assert account(site.db, "anna.p@example.com").phone is None


@pytest.mark.parametrize(
    ("changes", "words"),
    [
        ({"first_name": " "}, "անունը"),
        ({"last_name": ""}, "ազգանունը"),
        ({"email": "anna"}, "Email-ը սխալ է"),
        ({"email": "a" * 60 + "@x.am"}, "շատ երկար"),
        ({"phone": "12ab"}, "Հեռախոսահամարը սխալ է"),
        ({"password_again": "something-else"}, "չեն համընկնում"),
        ({"password": "short", "password_again": "short"}, "առնվազն 10 նիշ"),
    ],
)
def test_mistakes_are_explained_and_the_form_is_kept(site, changes, words):
    _, response = register(site, **changes)
    assert response.status_code == 422 and words in response.text
    assert 'value="Պետրոսյան"' in response.text or changes.get("last_name") == ""
    assert "anna-password-1" not in response.text  # a password is never sent back
    with site.db.transaction() as s:
        assert s.query(StaffUser).count() == 1  # only the admin


def test_an_email_has_one_account(site):
    register(site)
    _, response = register(site, email="anna.p@example.com")
    assert response.status_code == 409 and texts.EMAIL_TAKEN in response.text


def test_one_computer_makes_only_a_few_accounts_an_hour(site):
    for number in range(5):
        _, response = register(site, email=f"user{number}@example.com")
        assert response.status_code == 303
    _, response = register(site, email="user9@example.com")
    assert response.status_code == 429 and texts.REGISTER_THROTTLED in response.text


def test_registration_can_be_turned_off(site, write_config):
    write_config("[web]\nregistration = false\n")
    site.cfg = load_config(site.project / "config" / "reel.toml")
    app = create_app(site.db, lambda: site.cfg, SECRET, throttle=LoginThrottle())
    with TestClient(app, follow_redirects=False) as client:
        assert 'href="/register"' not in client.get("/login").text
        assert client.get("/register").status_code == 404


def test_registration_values():
    found = parse_registration("  Աննա ", "Պետրոսյան", " A@B.am ", "", "x" * 10, "x" * 10)
    assert (found.first_name, found.email, found.phone, found.display_name) == (
        "Աննա", "a@b.am", None, "Աննա Պետրոսյան"
    )  # fmt: skip


# --- what a user sees and may do -------------------------------------------------------------


def test_a_user_sees_only_their_own_recordings(site):
    client, _ = register(site)
    me = account(site.db, "anna.p@example.com").id
    mine = own_job(site.db, me, name="mine.mp4")
    other = add_job(site.db, name="other.mp4", group="Python 12", lesson=2)
    listing = client.get("/").text
    assert f"/recordings/{mine}" in listing and f"/recordings/{other}" not in listing
    assert client.get(f"/recordings/{mine}").status_code == 200
    for path in (f"/recordings/{other}", f"/recordings/{other}/video",
                 f"/recordings/{other}/thumbnail"):  # fmt: skip
        assert client.get(path).status_code == 404
    data = {"csrf_token": token(client, f"/recordings/{mine}")}
    assert client.post(f"/recordings/{other}/cancel", data=data).status_code == 404
    admin = login(site, "admin", ADMIN_PASSWORD)
    listing = admin.get("/").text
    assert f"/recordings/{mine}" in listing and f"/recordings/{other}" in listing


def test_a_users_own_video_is_only_processed_and_can_be_downloaded(site):
    client, _ = register(site)
    me = account(site.db, "anna.p@example.com").id
    job_id = own_job(site.db, me)
    video = site.cfg.paths.data_dir / "jobs" / str(job_id) / "cleaned.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    page = client.get(f"/recordings/{job_id}").text
    assert "Միայն մշակում" in page and f"/recordings/{job_id}/video?download=1" in page
    assert "<h2>Հրապարակում</h2>" not in page  # nothing is published
    assert client.get(f"/recordings/{job_id}/video?download=1").status_code == 200


def test_the_page_says_until_when_a_users_video_is_kept_and_when_it_went(site):
    """T-1014: a user's processed video is deleted 30 days after it is ready."""
    client, _ = register(site)
    assert "Մշակված տեսանյութը կպահվի 30 օր" in client.get("/upload").text
    me = account(site.db, "anna.p@example.com").id
    finished = utcnow() - timedelta(days=29)
    job_id = own_job(site.db, me, finished_at=finished)
    video = site.cfg.paths.data_dir / "jobs" / str(job_id) / "cleaned.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    until = local_text(finished + timedelta(days=30), ZoneInfo(site.cfg.settings.schedule.timezone))
    page = client.get(f"/recordings/{job_id}").text
    assert f"ներբեռնեք այն մինչև <strong>{until}</strong>" in page
    cleanup(site.db, site.cfg, now=finished + timedelta(days=31))
    page = client.get(f"/recordings/{job_id}").text
    assert "Մշակված տեսանյութը և վերբեռնված ֆայլերը ջնջվել են" in page
    assert "?download=1" not in page
    assert client.get(f"/recordings/{job_id}/video").status_code == 404


def test_a_user_cannot_use_the_admins_pages_and_actions(site):
    client, _ = register(site)
    me = account(site.db, "anna.p@example.com").id
    job_id = own_job(site.db, me, status=JobStatus.ON_HOLD, stage=Stage.CONSENT,
                     hold_reason="consent_opted_out")  # fmt: skip
    for path in ("/metrics", "/metrics.json", "/admin/users", "/admin/users/1"):
        response = client.get(path)
        assert response.status_code == 403, path
    assert texts.ADMIN_ONLY in client.get("/metrics").text
    page = client.get(f"/recordings/{job_id}").text
    assert "Ընդունել և շարունակել" not in page and "Վերագործարկել" not in page
    data = {"csrf_token": token(client, f"/recordings/{job_id}"), "advance": "1"}
    assert client.post(f"/recordings/{job_id}/release", data=data).status_code == 403
    assert client.post(f"/recordings/{job_id}/rerun", data=data).status_code == 403
    with site.db.transaction() as s:
        assert s.get(Job, job_id).status is JobStatus.ON_HOLD
    # their own decisions they make themselves
    response = client.post(f"/recordings/{job_id}/cancel", data=data)
    assert response.status_code == 303
    with site.db.transaction() as s:
        assert s.get(Job, job_id).status is JobStatus.CANCELLED


def test_a_users_group_is_not_added_to_groups_toml(site):
    client, _ = register(site)
    me = account(site.db, "anna.p@example.com").id
    job_id = own_job(site.db, me, status=JobStatus.ON_HOLD, stage=Stage.CHAPTERS,
                     hold_reason="identification")  # fmt: skip
    data = {"csrf_token": token(client, f"/recordings/{job_id}"), "group": "Իմ խումբը",
            "lesson": "3"}  # fmt: skip
    assert client.post(f"/recordings/{job_id}/identify", data=data).status_code == 303
    with site.db.transaction() as s:
        assert s.get(Job, job_id).recording.group_name == "Իմ խումբը"
    assert "Իմ խումբը" not in (site.project / "config" / "groups.toml").read_text("utf-8")


# --- uploads ---------------------------------------------------------------------------------


def upload(client, sizes=(10,), group="Python 12"):
    headers = {"X-CSRF-Token": token(client, "/upload")}
    body = {"group": group, "lesson": "4", "date": "", "note": "",
            "files": [{"name": f"part{i}.mp4", "size": size, "modified_ms": 0}
                      for i, size in enumerate(sizes)]}  # fmt: skip
    started = client.post("/uploads", content=json.dumps(body), headers=headers)
    assert started.status_code == 201, started.text
    upload_id = started.json()["id"]
    for index, size in enumerate(sizes):
        sent = client.put(f"/uploads/{upload_id}/files/{index}?offset=0", content=b"x" * size,
                          headers=headers)  # fmt: skip
        assert sent.status_code == 200
    return upload_id, headers


def finish(client, upload_id, headers):
    response = client.post(f"/uploads/{upload_id}/finish", headers=headers)
    assert response.status_code == 200, response.text
    with_db = response.json()["job_id"]
    return with_db


def recording_of(db, job_id):
    with db.transaction() as s:
        recording = s.get(Job, job_id).recording
        return recording.uploaded_by_id, recording.publish, recording.group_name


def test_a_users_upload_is_theirs_and_only_processed(site):
    client, _ = register(site)
    me = account(site.db, "anna.p@example.com").id
    assert "կմշակվի, բայց չի տեղադրվի YouTube-ում" in client.get("/upload").text
    upload_id, headers = upload(client, group="Նոր խումբ")
    # another account cannot see or finish it
    stranger, _ = register(site, email="other@example.com")
    other = {"X-CSRF-Token": token(stranger, "/upload")}
    assert stranger.get(f"/uploads/{upload_id}").status_code == 404
    assert stranger.post(f"/uploads/{upload_id}/finish", headers=other).status_code == 404
    assert stranger.delete(f"/uploads/{upload_id}", headers=other).status_code == 404
    job_id = finish(client, upload_id, headers)
    assert recording_of(site.db, job_id) == (me, False, "Նոր խումբ")
    assert "Նոր խումբ" not in (site.project / "config" / "groups.toml").read_text("utf-8")


def test_the_admin_lets_a_user_publish(site):
    client, _ = register(site)
    me = account(site.db, "anna.p@example.com").id
    admin = login(site, "admin", ADMIN_PASSWORD)
    data = {"csrf_token": token(admin, f"/admin/users/{me}"), "allow": "1"}
    response = admin.post(f"/admin/users/{me}/publish", data=data)
    assert response.headers["location"] == f"/admin/users/{me}?done=publish_on"
    assert texts.ACCOUNT_MESSAGES["publish_on"] in admin.get(response.headers["location"]).text
    assert account(site.db, "anna.p@example.com").can_publish
    assert "կմշակվի, բայց չի տեղադրվի" not in client.get("/upload").text  # at once
    job_id = finish(client, *upload(client, group="Java 9"))
    assert recording_of(site.db, job_id) == (me, True, "Java 9")
    assert '[groups."Java 9"]' in (site.project / "config" / "groups.toml").read_text("utf-8")
    data["allow"] = "0"
    admin.post(f"/admin/users/{me}/publish", data=data)
    assert not account(site.db, "anna.p@example.com").can_publish


def test_an_admins_upload_is_published(site):
    admin = login(site, "admin", ADMIN_PASSWORD)
    job_id = finish(admin, *upload(admin))
    assert recording_of(site.db, job_id) == (account(site.db, "admin").id, True, "Python 12")


# --- the admin's account pages ---------------------------------------------------------------


def test_the_admin_sees_every_account_and_what_it_did(site):
    client, _ = register(site)
    me = account(site.db, "anna.p@example.com").id
    job_id = own_job(site.db, me, status=JobStatus.ON_HOLD, stage=Stage.CONSENT,
                     hold_reason="consent_opted_out")  # fmt: skip
    data = {"csrf_token": token(client, f"/recordings/{job_id}"), "note": "Կրկնօրինակ"}
    client.post(f"/recordings/{job_id}/cancel", data=data)
    admin = login(site, "admin", ADMIN_PASSWORD)
    listing = admin.get("/admin/users").text
    assert "Աննա Պետրոսյան" in listing and "anna.p@example.com" in listing
    assert "+374 91 123456" in listing and "Միայն մշակում" in listing
    page = admin.get(f"/admin/users/{me}").text
    assert f'href="/recordings/{job_id}"' in page
    assert "Կրկնօրինակ" in page  # the cancel, with the user's note
    assert "Թույլ տալ տեղադրել YouTube-ում" in page and "Փակել հաշիվը" in page
    assert admin.get("/admin/users/999").status_code == 404


def test_the_admin_closes_and_opens_an_account(site):
    client, _ = register(site)
    me = account(site.db, "anna.p@example.com").id
    admin = login(site, "admin", ADMIN_PASSWORD)
    data = {"csrf_token": token(admin, f"/admin/users/{me}"), "active": "0"}
    response = admin.post(f"/admin/users/{me}/active", data=data)
    assert response.headers["location"] == f"/admin/users/{me}?done=closed"
    assert client.get("/").status_code == 303  # the open session ended at once
    login_page = site.client()
    attempt = {"username": "anna.p@example.com", "password": FORM["password"],
               "csrf_token": token(login_page)}  # fmt: skip
    assert login_page.post("/login", data=attempt).status_code == 401
    data["active"] = "1"
    admin.post(f"/admin/users/{me}/active", data=data)
    login(site, "anna.p@example.com", FORM["password"])


def test_the_admin_cannot_close_their_own_account_or_stop_an_admin_publishing(site):
    admin = login(site, "admin", ADMIN_PASSWORD)
    me = account(site.db, "admin").id
    data = {"csrf_token": token(admin, f"/admin/users/{me}"), "active": "0", "allow": "0"}
    closing = admin.post(f"/admin/users/{me}/active", data=data)
    assert closing.status_code == 409 and texts.CANNOT_CLOSE_SELF in closing.text
    publishing = admin.post(f"/admin/users/{me}/publish", data=data)
    assert publishing.status_code == 409 and texts.ADMIN_PUBLISHES in publishing.text
    assert account(site.db, "admin").is_active


def test_the_admin_sees_who_uploaded_a_recording(site):
    register(site)
    me = account(site.db, "anna.p@example.com").id
    job_id = own_job(site.db, me)
    admin = login(site, "admin", ADMIN_PASSWORD)
    page = admin.get(f"/recordings/{job_id}").text
    assert "Վերբեռնել է" in page and "Աննա Պետրոսյան" in page
    with site.db.transaction() as s:
        assert s.get(Recording, s.get(Job, job_id).recording_id).publish is False


def test_a_duplicate_says_why_without_another_accounts_details(site):
    """T-1015: the page tells why a video was not processed again."""
    client, _ = register(site)
    me = account(site.db, "anna.p@example.com").id
    admins = add_job(site.db, group="Python 12", lesson=1, status=JobStatus.DONE, stage=Stage.POST)
    mine = own_job(site.db, me)
    others, own = (own_job(site.db, me, status=JobStatus.CANCELLED, stage=Stage.IMPORT)
                   for _ in range(2))  # fmt: skip
    with site.db.transaction() as s:
        s.get(Job, others).recording.duplicate_of_id = s.get(Job, admins).recording_id
        s.get(Job, own).recording.duplicate_of_id = s.get(Job, mine).recording_id
    page = client.get(f"/recordings/{others}").text
    assert "Այս տեսանյութն արդեն կա Reel-ում, ուստի կրկին չի մշակվել։" in page
    assert f'href="/recordings/{admins}"' not in page
    link = f'Այս տեսանյութն արդեն վերբեռնված է՝ <a href="/recordings/{mine}">#{mine}</a>'
    assert link in client.get(f"/recordings/{own}").text
    admin = login(site, "admin", ADMIN_PASSWORD)
    page = admin.get(f"/recordings/{others}").text
    assert f'<a href="/recordings/{admins}">#{admins}</a>' in page
