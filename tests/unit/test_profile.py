"""Own-account profiles, email sign-in, password sessions and admin-only cost (T-1016)."""

import html
import logging
import secrets
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from test_accounts import ADMIN_PASSWORD, account, login, own_job, register, token
from test_accounts import FORM as REGISTRATION_FORM

from reel.config import load_config
from reel.db import (
    ROLE_ADMIN,
    ROLE_USER,
    Job,
    JobStatus,
    StaffUser,
    Stage,
    StageRun,
    init_database,
    normalize_username,
    open_database,
    utcnow,
)
from reel.db.enums import StageRunStatus
from reel.web import create_app, texts
from reel.web.app import _check_login
from reel.web.forms import parse_new_password, parse_profile, parse_registration
from reel.web.security import LoginThrottle, hash_password, verify_password
from reel.web.views import local_text

FORM = {**REGISTRATION_FORM, "email": normalize_username(REGISTRATION_FORM["email"])}


def require(condition: bool, message: str = "Profile assertion failed") -> None:
    """Report an invariant failure without printing personal or secret values."""
    if not condition:
        raise AssertionError(message)


@dataclass(repr=False)
class Site:
    db: Any
    cfg: Any
    app: Any
    client: Any
    project: Any


@pytest.fixture
def site(project, write_config):
    write_config("")
    write_config('[groups."Python 12"]\n', "groups.toml")
    cfg = load_config(project / "config" / "reel.toml", environ={})
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    with db.transaction() as session:
        session.add(
            StaffUser(
                username="admin",
                password_hash=hash_password(ADMIN_PASSWORD),
                role=ROLE_ADMIN,
                display_name="Admin",
            )
        )
    app = create_app(db, lambda: cfg, secrets.token_urlsafe(40), throttle=LoginThrottle())
    with ExitStack() as stack:

        def client():
            return stack.enter_context(TestClient(app, follow_redirects=False))

        yield Site(db, cfg, app, client, project)
    db.dispose()


def user(site):
    client, response = register(site)
    require(response.status_code == 303, "Registration fixture failed")
    return client, account(site.db, FORM["email"]).id


def address() -> str:
    return secrets.token_hex(8) + "@" + "example.test"


def details(client, **changes):
    data = {
        "first_name": "Նոր",
        "last_name": "Անուն",
        "email": FORM["email"],
        "phone": FORM["phone"],
        **changes,
        "csrf_token": token(client, "/profile"),
    }
    return client.post("/profile/details", data=data)


def password(client, current=None, new=None, again=None):
    current = FORM["password"] if current is None else current
    new = secrets.token_urlsafe(18) if new is None else new
    again = new if again is None else again
    return client.post(
        "/profile/password",
        data={
            "csrf_token": token(client, "/profile"),
            "current_password": current,
            "new_password": new,
            "password_again": again,
        },
    )


def page_values(page: str) -> dict[str, str]:
    class Inputs(HTMLParser):
        values = {}

        def handle_starttag(self, tag, attrs):
            found = dict(attrs)
            if tag == "input" and found.get("name"):
                self.values[found["name"]] = found.get("value", "")

    parser = Inputs()
    parser.feed(page)
    return parser.values


def visible(page: str) -> str:
    class Words(HTMLParser):
        def __init__(self):
            super().__init__()
            self.words = []

        def handle_data(self, data):
            self.words.append(data)

    parser = Words()
    parser.feed(page)
    return " ".join(parser.words)


@pytest.mark.parametrize("role", ["admin", "user", "publisher"])
def test_each_account_sees_only_its_own_profile_and_local_dates(site, role):
    client, ident = user(site)
    if role == "admin":
        client = login(site, "admin", ADMIN_PASSWORD)
        ident = account(site.db, "admin").id
    moment = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    with site.db.transaction() as session:
        found = session.get(StaffUser, ident)
        found.created_at = moment
        found.last_login_at = moment + timedelta(hours=1)
        found.can_publish = role == "publisher"
    response = client.get("/profile")
    require(response.status_code == 200)
    record = account(site.db, "admin" if role == "admin" else FORM["email"])
    form = page_values(response.text)
    require(form["first_name"] == (record.first_name or ""))
    require(form["last_name"] == (record.last_name or ""))
    require(form["email"] == (record.email or ""))
    require(form["phone"] == (record.phone or ""))
    require(html.escape(record.username) in response.text)
    require(texts.ROLE_LABELS[record.role] in response.text)
    label = "Միայն մշակում" if role == "user" else "Տեղադրում է YouTube-ում"
    require(label in response.text)
    zone = ZoneInfo(site.cfg.settings.schedule.timezone)
    require(local_text(moment, zone) in response.text)
    require(local_text(moment + timedelta(hours=1), zone) in response.text)
    for key in ("current_password", "new_password", "password_again"):
        require(form[key] == "", "A password was prefilled")


def test_anonymous_and_closed_accounts_cannot_open_profile(site):
    anonymous = site.client().get("/profile")
    require(anonymous.status_code == 303 and "/login" in anonymous.headers["location"])
    client, ident = user(site)
    with site.db.transaction() as session:
        session.get(StaffUser, ident).is_active = False
    require(client.get("/profile").status_code == 303)


@pytest.mark.parametrize("role", ["admin", "user"])
def test_profile_navigation_and_header_link_for_both_roles(site, role):
    client = login(site, "admin", ADMIN_PASSWORD) if role == "admin" else user(site)[0]
    page = client.get("/profile").text
    require('class="nav-link is-active" href="/profile"' in page)
    require('class="who" href="/profile"' in page)
    require("Իմ էջը" in page)
    require('class="nav-link is-active" href="/profile"' not in client.get("/upload").text)


@pytest.mark.parametrize("role", ["admin", "user"])
def test_details_save_normalized_values_and_immediately_update_header_and_admin_view(site, role):
    client, ident = user(site)
    username = FORM["email"]
    if role == "admin":
        client = login(site, "admin", ADMIN_PASSWORD)
        username, ident = "admin", account(site.db, "admin").id
    before = account(site.db, username)
    changed = address()
    result = details(
        client,
        first_name="  Անուն   Նոր ",
        last_name="  Ազգանուն ",
        email=" " + changed.upper() + " ",
        phone="  " + FORM["phone"] + "  ",
    )
    require(result.status_code == 303 and result.headers["location"] == "/profile?done=details")
    current = account(site.db, username)
    require(current.first_name == "Անուն Նոր" and current.last_name == "Ազգանուն")
    require(current.email == changed and current.phone == FORM["phone"])
    require(current.display_name == "Անուն Նոր Ազգանուն")
    require(
        (current.username, current.role, current.can_publish, current.is_active)
        == (before.username, before.role, before.can_publish, before.is_active)
    )
    page = client.get(result.headers["location"]).text
    require(texts.PROFILE_MESSAGES["details"] in page)
    require('class="who" href="/profile">Անուն Նոր Ազգանուն</a>' in page)
    admin = login(site, "admin", ADMIN_PASSWORD)
    page = admin.get(f"/admin/users/{ident}").text
    require(html.escape(changed) in page and html.escape(FORM["phone"]) in page)
    require("Անուն Նոր Ազգանուն" in page)


def test_phone_can_be_cleared_and_own_current_email_is_not_a_collision(site):
    client, _ = user(site)
    require(details(client, phone=" ").status_code == 303)
    require(account(site.db, FORM["email"]).phone is None)


@pytest.mark.parametrize(
    "case",
    [
        "first-empty",
        "last-empty",
        "email-invalid",
        "phone-invalid",
        "email-required",
        "first-long",
        "last-long",
        "email-long",
        "email-characters",
    ],
)
def test_details_errors_keep_typed_values_and_do_not_write(site, case):
    client, _ = user(site)
    changed = {
        "first-empty": {"first_name": " "},
        "last-empty": {"last_name": ""},
        "email-invalid": {"email": "invalid"},
        "phone-invalid": {"phone": "12ab"},
        "email-required": {"email": ""},
        "first-long": {"first_name": "Ա" * 101},
        "last-long": {"last_name": "Ա" * 101},
        "email-long": {"email": "x" * 60 + "@" + "x.am"},
        "email-characters": {"email": "x/y@" + "example.test"},
    }[case]
    typed = {**FORM, **changed}
    try:
        parse_profile(
            typed["first_name"] if "first_name" in changed else "Նոր",
            typed["last_name"] if "last_name" in changed else "Անուն",
            typed["email"],
            typed["phone"],
            email_required=True,
        )
    except ValueError as exc:
        expected = str(exc)
    else:
        raise AssertionError("The invalid fixture was accepted")
    response = details(client, **changed)
    require(response.status_code == 422 and expected in response.text)
    form = page_values(response.text)
    defaults = {
        "first_name": "Նոր",
        "last_name": "Անուն",
        "email": FORM["email"],
        "phone": FORM["phone"],
    }
    require(all(form[key] == value for key, value in {**defaults, **changed}.items()))
    record = account(site.db, FORM["email"])
    require(record.first_name == FORM["first_name"] and record.email == FORM["email"])


def test_a_legacy_admin_can_clear_optional_email(site):
    client = login(site, "admin", ADMIN_PASSWORD)
    require(details(client, email="", phone="").status_code == 303)
    record = account(site.db, "admin")
    require(record.email is None and record.phone is None)
    require('id="email" name="email" type="email" required' not in client.get("/profile").text)


@pytest.mark.parametrize("collision", ["email", "username"])
@pytest.mark.parametrize("active", [True, False], ids=["active", "closed"])
def test_another_accounts_email_or_username_cannot_be_taken(site, collision, active):
    client, _ = user(site)
    taken = address()
    with site.db.transaction() as session:
        session.add(
            StaffUser(
                username=taken if collision == "username" else "other",
                email=taken if collision == "email" else None,
                password_hash=hash_password(secrets.token_urlsafe(18)),
                is_active=active,
            )
        )
    response = details(client, email=taken)
    require(response.status_code == 409 and texts.EMAIL_TAKEN in response.text)
    require(page_values(response.text)["email"] == taken)
    require(account(site.db, FORM["email"]).email == FORM["email"])


def test_changed_email_and_immutable_username_both_sign_in(site):
    client, _ = user(site)
    changed = address()
    require(details(client, email=changed).status_code == 303)
    require(login(site, changed.upper(), FORM["password"]).get("/profile").status_code == 200)
    require(login(site, FORM["email"], FORM["password"]).get("/profile").status_code == 200)
    require(account(site.db, FORM["email"]).username == FORM["email"])
    other, response = register(site, email=changed)
    require(response.status_code == 409 and texts.EMAIL_TAKEN in response.text)
    response = other.post("/register", data={**FORM, "csrf_token": token(other, "/register")})
    require(response.status_code == 409, "An earlier username email was reusable")


def test_a_previous_contact_email_stops_signing_in_after_a_second_change(site):
    client, _ = user(site)
    old_contact, new_contact = address(), address()
    require(details(client, email=old_contact).status_code == 303)
    require(details(client, email=new_contact).status_code == 303)
    attempt = site.client()
    response = attempt.post(
        "/login",
        data={
            "username": old_contact,
            "password": FORM["password"],
            "csrf_token": token(attempt),
        },
    )
    require(response.status_code == 401)
    require(login(site, new_contact, FORM["password"]).get("/profile").status_code == 200)


def test_username_has_priority_over_another_accounts_email_with_one_lookup(site):
    client, _ = user(site)
    different = secrets.token_urlsafe(18)
    with site.db.transaction() as session:
        session.query(StaffUser).filter_by(username=FORM["email"]).one().email = address()
        session.flush()
        session.add(
            StaffUser(username="other", email=FORM["email"], password_hash=hash_password(different))
        )
    queries = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(site.db.engine, "before_cursor_execute", capture)
    try:
        require(_check_login(site.db, FORM["email"], FORM["password"]) is not None)
        require(len(queries) == 1, "Login did not use one lookup")
        require(_check_login(site.db, FORM["email"], different) is None)
    finally:
        event.remove(site.db.engine, "before_cursor_execute", capture)
    require(client.get("/profile").status_code == 200)


@pytest.mark.parametrize("invalid", [False, True], ids=["missing", "invalid-identifier"])
def test_missing_accounts_still_run_dummy_password_verification(site, monkeypatch, invalid):
    calls = []

    def verify(_password, stored):
        calls.append(stored is None)
        return False

    monkeypatch.setattr("reel.web.app.verify_password", verify)
    require(_check_login(site.db, "/" if invalid else "missing", secrets.token_urlsafe(18)) is None)
    require(calls == [True])


def test_email_changes_preserve_action_history_and_account_identity(site):
    client, ident = user(site)
    job_id = own_job(site.db, ident, status=JobStatus.QUEUED, stage=Stage.IMPORT)
    response = client.post(
        f"/recordings/{job_id}/cancel",
        data={
            "csrf_token": token(client, f"/recordings/{job_id}"),
            "note": "PROFILE-AUDIT-NOTE",
        },
    )
    require(response.status_code == 303)
    require(details(client, email=address()).status_code == 303)
    admin = login(site, "admin", ADMIN_PASSWORD)
    page = admin.get(f"/admin/users/{ident}").text
    require("PROFILE-AUDIT-NOTE" in page and f"/recordings/{job_id}" in page)
    with site.db.transaction() as session:
        require(session.get(Job, job_id).recording.uploaded_by_id == ident)


def test_supplied_account_ids_and_permissions_cannot_change_another_account(site):
    client, ident = user(site)
    admin_before = account(site.db, "admin")
    response = details(
        client,
        user_id=admin_before.id,
        username="replacement",
        role=ROLE_ADMIN,
        can_publish="1",
        is_active="0",
    )
    require(response.status_code == 303)
    current, admin_after = account(site.db, FORM["email"]), account(site.db, "admin")
    require(current.id == ident and current.role == ROLE_USER and not current.can_publish)
    require(admin_before.display_name == admin_after.display_name)
    require(client.get(f"/profile/{admin_before.id}").status_code == 404)


@pytest.mark.parametrize("case", ["current", "mismatch", "short", "unchanged"])
def test_password_checks_are_ordered_and_leave_the_hash_unchanged(site, case):
    client, _ = user(site)
    original = account(site.db, FORM["email"]).password_hash
    current = FORM["password"]
    new = secrets.token_urlsafe(18)
    again = new
    if case == "current":
        current, new, again = secrets.token_urlsafe(18), "x", "y"
        expected = texts.PROFILE_CURRENT_PASSWORD_WRONG
    elif case == "mismatch":
        new, again = "x", "y"
        expected = texts.PROFILE_PASSWORD_MISMATCH
    elif case == "short":
        new = again = "x"
        expected = texts.PROFILE_PASSWORD_RULES.format(minimum=10)
    else:
        new = again = current
        expected = texts.PROFILE_PASSWORD_UNCHANGED
    response = password(client, current, new, again)
    require(response.status_code == 422 and expected in response.text)
    require(account(site.db, FORM["email"]).password_hash == original)
    require(
        all(
            page_values(response.text)[field] == ""
            for field in ("current_password", "new_password", "password_again")
        )
    )


def test_wrong_current_passwords_share_the_login_throttle(site):
    client, _ = user(site)
    for _ in range(5):
        require(password(client, current=secrets.token_urlsafe(18)).status_code == 422)
    response = password(client)
    require(response.status_code == 429 and texts.LOGIN_THROTTLED in response.text)
    throttle = site.app.state.reel.throttle
    require(throttle.blocked(f"user:{FORM['email'].casefold()}"))
    require(throttle.blocked("client:testclient"))


def test_login_failures_also_block_password_changes(site):
    client, _ = user(site)
    attempt = site.client()
    for _ in range(5):
        response = attempt.post(
            "/login",
            data={
                "username": FORM["email"],
                "password": secrets.token_urlsafe(18),
                "csrf_token": token(attempt),
            },
        )
        require(response.status_code == 401)
    require(password(client).status_code == 429)


@pytest.mark.parametrize("role", ["admin", "user"])
def test_password_change_keeps_this_client_ends_other_sessions_and_replaces_login(site, role):
    client, _ = user(site)
    username, old = FORM["email"], FORM["password"]
    if role == "admin":
        username, old = "admin", ADMIN_PASSWORD
        client = login(site, username, old)
    second = login(site, username, old)
    new = secrets.token_urlsafe(18)
    response = password(client, old, new)
    require(
        response.status_code == 303 and response.headers["location"] == "/profile?done=password"
    )
    page = client.get(response.headers["location"])
    require(page.status_code == 200 and texts.PROFILE_MESSAGES["password"] in page.text)
    require(second.get("/profile").status_code == 303)
    require(verify_password(new, account(site.db, username).password_hash))
    attempt = site.client()
    response = attempt.post(
        "/login",
        data={
            "username": username,
            "password": old,
            "csrf_token": token(attempt),
        },
    )
    require(response.status_code == 401)
    require(login(site, username, new).get("/profile").status_code == 200)


@pytest.mark.parametrize("route", ["details", "password"])
def test_profile_posts_require_csrf(site, route):
    client, _ = user(site)
    before = account(site.db, FORM["email"])
    response = client.post("/profile/" + route, data={})
    require(response.status_code == 403 and texts.CSRF_FAILED in response.text)
    after = account(site.db, FORM["email"])
    require(before.password_hash == after.password_hash and before.first_name == after.first_name)


@pytest.mark.parametrize("field", ["current_password", "new_password", "password_again"])
def test_outer_password_limits_never_echo_the_rejected_input(site, field):
    client, _ = user(site)
    secret = secrets.token_urlsafe(800)
    values = {"current_password": FORM["password"], "new_password": secrets.token_urlsafe(18)}
    values["password_again"] = values["new_password"]
    values[field] = secret
    response = client.post(
        "/profile/password",
        data={
            "csrf_token": token(client, "/profile"),
            **values,
        },
    )
    require(response.status_code == 422 and texts.PROFILE_FORM_INVALID in response.text)
    require(secret not in response.text, "Oversized secret was reflected")
    require(all(page_values(response.text)[key] == "" for key in values))


def test_outer_details_limits_keep_safe_fields_and_never_echo_extra_password_fields(site):
    client, _ = user(site)
    secret = secrets.token_urlsafe(18)
    typed = "Ա" * 201
    response = details(client, first_name=typed, password=secret)
    require(response.status_code == 422 and texts.PROFILE_FORM_INVALID in response.text)
    require(page_values(response.text)["first_name"] == typed)
    require(secret not in response.text)


def test_profile_logs_only_ids_and_never_reflects_passwords_or_hashes(site, caplog):
    client, ident = user(site)
    current = FORM["password"]
    new = secrets.token_urlsafe(18)
    before = account(site.db, FORM["email"]).password_hash
    caplog.set_level(logging.INFO, logger="reel.web")
    caplog.clear()
    contact = address()
    saved = details(client, email=contact)
    changed = password(client, new=new)
    page = client.get("/profile")
    require(saved.status_code == 303 and changed.status_code == 303 and page.status_code == 200)
    after = account(site.db, FORM["email"]).password_hash
    records = [r.getMessage() for r in caplog.records if r.name == "reel.web"]
    require(
        records == [f"account {ident} updated its details", f"account {ident} changed its password"]
    )
    require(
        all(
            value not in caplog.text
            for value in [current, new, before, after, FORM["email"], contact, FORM["phone"]]
        ),
        "A sensitive value reached profile logs",
    )
    require(
        all(
            value not in response.text
            for response in [saved, changed, page]
            for value in [current, new, before, after]
        ),
        "A password or hash reached a profile response",
    )


def test_profile_escapes_typed_markup(site):
    client, _ = user(site)
    unsafe = "<script>profile_probe</script>"
    response = details(client, first_name=unsafe, phone="invalid")
    require(response.status_code == 422)
    require(unsafe not in response.text and html.escape(unsafe, quote=True) in response.text)


@pytest.mark.parametrize("publishes", [False, True])
def test_cost_and_time_are_visible_only_to_the_admin(site, publishes):
    client, ident = user(site)
    with site.db.transaction() as session:
        session.get(StaffUser, ident).can_publish = publishes
    job_id = own_job(site.db, ident)
    now = utcnow()
    with site.db.transaction() as session:
        session.add(
            StageRun(
                job_id=job_id,
                stage=Stage.TRANSCRIBE,
                attempt=1,
                status=StageRunStatus.SUCCEEDED,
                started_at=now - timedelta(seconds=30),
                finished_at=now,
                metrics={"seconds": 30},
            )
        )
    user_page = client.get(f"/recordings/{job_id}")
    admin = login(site, "admin", ADMIN_PASSWORD)
    admin_page = admin.get(f"/recordings/{job_id}")
    require(user_page.status_code == admin_page.status_code == 200)
    user_text, admin_text = visible(user_page.text), visible(admin_page.text)
    for label in ["Ծախս և ժամանակ", "Whisper", "token", "բյուջե"]:
        require(label not in user_text, "Cost information reached a user's recording page")
        require(label in admin_text, "The admin lost cost information")


@pytest.mark.parametrize("case", ["first", "last", "email", "phone", "mismatch", "short"])
def test_shared_validators_preserve_registration_error_messages(case):
    values = {**FORM}
    if case == "first":
        values["first_name"] = ""
    elif case == "last":
        values["last_name"] = ""
    elif case == "email":
        values["email"] = "invalid"
    elif case == "phone":
        values["phone"] = "invalid"
    elif case == "mismatch":
        values["password_again"] = "x"
    else:
        values["password"] = values["password_again"] = "x"
    with pytest.raises(ValueError) as registration_error:
        parse_registration(
            values["first_name"],
            values["last_name"],
            values["email"],
            values["phone"],
            values["password"],
            values["password_again"],
        )
    with pytest.raises(ValueError) as shared_error:
        if case in ["mismatch", "short"]:
            parse_new_password(values["password"], values["password_again"])
        else:
            parse_profile(
                values["first_name"],
                values["last_name"],
                values["email"],
                values["phone"],
                email_required=True,
            )
    require(str(registration_error.value) == str(shared_error.value))
