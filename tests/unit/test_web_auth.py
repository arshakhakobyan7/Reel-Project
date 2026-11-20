"""Dashboard foundation: login, sessions, CSRF, throttling, accounts, ``reel serve`` (T-802)."""

import io
import re
import socket
import threading
import time
import urllib.request
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import select

from reel.auth.store import MemorySecretStore
from reel.cli import main
from reel.config import load_config
from reel.db import StaffUser, init_database, open_database
from reel.web import create_app, texts
from reel.web.app import COOKIE, safe_next
from reel.web.commands import SESSION_KEY, session_secret
from reel.web.security import (
    LoginThrottle,
    PasswordError,
    SessionSigner,
    hash_password,
    verify_password,
)

SECRET = "test-session-secret-" + "x" * 30
PASSWORD = "Ճիշտ-գաղտնաբառ-2026"
START = 1_800_000_000.0
TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def site(project, write_config):
    write_config("[web]\nsession_hours = 1\n")
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    with db.transaction() as s:
        s.add(StaffUser(username="Anna", display_name="Աննա <b>Պ.</b>",
                        password_hash=hash_password(PASSWORD)))  # fmt: skip
    now = [START]
    app = create_app(db, lambda: cfg, SECRET, clock=lambda: now[0],
                     throttle=LoginThrottle(clock=lambda: now[0]))  # fmt: skip
    with TestClient(app, follow_redirects=False) as client:
        yield SimpleNamespace(client=client, db=db, now=now, cfg=cfg, app=app)
    db.dispose()


def csrf(client, path="/login"):
    return TOKEN.search(client.get(path).text)[1]


def login(site, username="anna", password=PASSWORD, next="/"):
    data = {
        "username": username,
        "password": password,
        "next": next,
        "csrf_token": csrf(site.client),
    }
    return site.client.post("/login", data=data)


def set_user(db, **values):
    with db.transaction() as s:
        user = s.scalar(select(StaffUser))
        for name, value in values.items():
            setattr(user, name, value)


def test_anonymous_visitors_are_sent_to_the_login_page(site):
    assert site.client.get("/").headers["location"] == "/login"
    response = site.client.get("/?status=failed")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2F%3Fstatus%3Dfailed"
    page = site.client.get("/login")
    assert page.status_code == 200 and '<html lang="hy">' in page.text
    assert "Մուտք գործել" in page.text and TOKEN.search(page.text)
    assert site.client.get("/static/style.css").status_code == 200
    missing = site.client.get("/no-such-page")
    assert missing.status_code == 404 and texts.NOT_FOUND in missing.text


def test_a_wrong_password_and_an_unknown_user_get_the_same_answer(site):
    for username, password in (("anna", "wrong-password-1"), ("nobody", PASSWORD), ("<>", "x")):
        response = login(site, username, password)
        assert response.status_code == 401 and texts.LOGIN_FAILED in response.text
    assert site.client.get("/").status_code == 303


def test_login_opens_the_dashboard_and_logout_ends_it(site):
    site.client.get("/login")
    before = site.client.cookies[COOKIE]
    response = login(site)
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower() and "Path=/" in cookie
    assert site.client.cookies[COOKIE] != before  # a new session after login
    home = site.client.get("/")
    assert home.status_code == 200 and "<h1>Ձայնագրություններ</h1>" in home.text
    assert "Աննա &lt;b&gt;Պ.&lt;/b&gt;" in home.text  # escaped
    assert "Հերթում" in home.text and "Այս ընտրությամբ ձայնագրություն չկա" in home.text
    with site.db.transaction() as s:
        assert s.scalar(select(StaffUser)).last_login_at is not None
    assert site.client.get("/login").headers["location"] == "/"  # already signed in
    logout = site.client.post("/logout", data={"csrf_token": csrf(site.client, "/")})
    assert logout.status_code == 303 and logout.headers["location"] == "/login"
    assert site.client.get("/").status_code == 303


def test_every_post_needs_the_session_csrf_token(site):
    data = {"username": "anna", "password": PASSWORD}
    for token in (None, "", "forged-" + "t" * 40):
        form = data if token is None else {**data, "csrf_token": token}
        response = site.client.post("/login", data=form)
        assert response.status_code == 403 and texts.CSRF_FAILED in response.text
    with TestClient(site.app, follow_redirects=False) as other:
        foreign = csrf(other)  # a valid token, but of another visitor's session
    assert site.client.post("/login", data={**data, "csrf_token": foreign}).status_code == 403
    assert login(site).status_code == 303
    assert site.client.post("/logout").status_code == 403
    assert site.client.post("/logout", data={"csrf_token": "x" * 43}).status_code == 403
    assert site.client.get("/").status_code == 200  # still signed in
    header = {"X-CSRF-Token": csrf(site.client, "/")}
    assert site.client.post("/logout", headers=header).status_code == 303


def test_sessions_expire(site):
    assert login(site).status_code == 303
    site.now[0] += 3600 - 1
    assert site.client.get("/").status_code == 200
    site.now[0] += 2
    assert site.client.get("/").headers["location"] == "/login"


def test_a_forged_or_foreign_cookie_is_ignored(site):
    assert login(site).status_code == 303
    body, signature = site.client.cookies[COOKIE].split(".")
    assert site.client.get("/").status_code == 200

    def only(value):  # replace the browser's cookie with this one
        site.client.cookies.clear()
        site.client.cookies.set(COOKIE, value)

    only(body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + signature)
    assert site.client.get("/").status_code == 303
    other = SessionSigner("another-secret-" + "y" * 30, lifetime_seconds=3600,
                          clock=lambda: site.now[0])  # fmt: skip
    only(other.dumps(other.new(user_id=1, fingerprint="x")))
    assert site.client.get("/").status_code == 303
    only(body + "." + signature)  # the genuine cookie still works
    assert site.client.get("/").status_code == 200


def test_disabling_the_account_or_changing_its_password_ends_the_sessions(site):
    assert login(site).status_code == 303
    set_user(site.db, is_active=False)
    assert site.client.get("/").status_code == 303
    assert login(site).status_code == 401  # a disabled account cannot sign in
    set_user(site.db, is_active=True)
    assert site.client.get("/").status_code == 303  # the old session stays ended
    assert login(site).status_code == 303
    set_user(site.db, password_hash=hash_password("another-password-1"))
    assert site.client.get("/").status_code == 303


def test_repeated_failures_are_throttled(site):
    for _ in range(5):
        assert login(site, password="wrong-password-1").status_code == 401
    refused = login(site)  # even the right password
    assert refused.status_code == 429 and texts.LOGIN_THROTTLED in refused.text
    site.now[0] += 15 * 60 + 1
    assert login(site).status_code == 303


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/?status=failed", "/?status=failed"),
        ("//evil.example/x", "/"),
        ("/\\evil.example", "/"),
        ("https://evil.example/", "/"),
        ("javascript:alert(1)", "/"),
        ("", "/"),
    ],
)
def test_login_only_goes_back_to_local_pages(value, expected):
    assert safe_next(value) == expected


def test_login_redirects_to_the_requested_page(site):
    assert login(site, next="//evil.example/").headers["location"] == "/"
    site.client.post("/logout", data={"csrf_token": csrf(site.client, "/")})
    assert login(site, next="/?status=failed").headers["location"] == "/?status=failed"


def test_security_headers(site):
    response = site.client.get("/login")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    policy = response.headers["content-security-policy"]
    assert "script-src 'self';" in policy and "unsafe-inline" not in policy
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert "openapi" not in site.client.get("/openapi.json").text


def test_password_hashes():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first.startswith("scrypt$32768$8$1$") and first != second  # a salt each time
    assert verify_password(PASSWORD, first) and not verify_password(PASSWORD + "x", first)
    assert not verify_password(PASSWORD, None) and not verify_password(PASSWORD, "md5$abc")
    assert not verify_password(PASSWORD, "scrypt$x$8$1$aa$bb")
    with pytest.raises(PasswordError):
        hash_password("short")


def test_reel_user_commands(project, write_config, monkeypatch, capsys):
    write_config("")
    config = str(project / "config" / "reel.toml")
    init_database(load_config(config).paths.database_file)

    def run(*args, stdin=""):
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        return main(["--config", config, "user", *args])

    assert run("add", "Aram", "--name", "Արամ", "--password-stdin", stdin=PASSWORD + "\n") == 0
    assert run("add", "aram", "--password-stdin", stdin=PASSWORD + "\n") == 1  # exists
    assert run("add", "bob", "--password-stdin", stdin="short\n") == 1
    assert run("disable", "ARAM") == 0 and run("disable", "nobody") == 1
    capsys.readouterr()
    assert run("list") == 0
    out = capsys.readouterr().out
    assert "aram" in out and "disabled" in out and "Արամ" in out and "bob" not in out
    assert run("enable", "aram") == 0
    assert run("password", "aram", "--password-stdin", stdin="new-password-2026\n") == 0
    db = open_database(load_config(config).paths.database_file)
    with db, db.transaction() as s:
        user = s.scalar(select(StaffUser))
        assert user.is_active and verify_password("new-password-2026", user.password_hash)
    assert PASSWORD not in capsys.readouterr().out


def test_the_session_secret_comes_from_env_or_the_secret_store(project, write_config):
    write_config("")
    cfg = load_config(project / "config" / "reel.toml")
    store = MemorySecretStore()
    made = session_secret(cfg, store)
    assert len(made) >= 32 and store.get(SESSION_KEY) == made
    assert session_secret(cfg, store) == made  # the same after a restart
    (project / ".env").write_text("REEL_WEB_SESSION_SECRET=" + SECRET + "\n", encoding="utf-8")
    cfg = load_config(project / "config" / "reel.toml")
    assert session_secret(cfg, store) == SECRET


def test_reel_serve_starts_the_dashboard_with_the_configured_address(
    project, write_config, monkeypatch
):
    write_config('[web]\nhost = "127.0.0.1"\nport = 8099\n[auth]\nstore = "file"\n')
    init_database(load_config(project / "config" / "reel.toml").paths.database_file)
    started = {}
    monkeypatch.setattr(uvicorn.Server, "run", lambda self: started.update(config=self.config))
    assert main(["--config", str(project / "config" / "reel.toml"), "serve", "--port", "8100"]) == 0
    assert started["config"].host == "127.0.0.1" and started["config"].port == 8100
    assert (project / "data" / "secrets.dev.json").is_file()  # the secret was made and kept


def test_the_dashboard_answers_over_real_http(site):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(site.app, host="127.0.0.1", port=port,
                                           log_config=None, lifespan="off"))  # fmt: skip
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=5) as response:
            page = response.read().decode("utf-8")
            assert response.status == 200 and "Մուտք գործել" in page
            assert "HttpOnly" in response.headers["set-cookie"]
    finally:
        server.should_exit = True
        thread.join(10)
