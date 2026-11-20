"""Secret store and Google OAuth with a fake Google (T-701). No network, no real account."""

import base64
import hashlib
import json
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from reel.auth import (
    SCOPES,
    AuthError,
    FileSecretStore,
    GoogleTokens,
    MemorySecretStore,
    OAuthClient,
    SecretStoreError,
    WindowsCredentialStore,
    authorize,
    connection,
    forget,
)
from reel.auth.google import STORE_KEY
from reel.cli import main
from reel.publish.google_api import GoogleApiClient

REFRESH = "1//refresh-" + "r" * 30
SECRET = "client-secret-" + "s" * 20


class FakeGoogle:
    """The token endpoint: checks PKCE and the redirect address, counts refreshes."""

    def __init__(self) -> None:
        self.challenge = ""
        self.redirect = ""
        self.refreshes = 0
        self.fail: str | None = None  # "invalid_grant", "server"
        self.rotate = False
        google = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                form = dict(urllib.parse.parse_qsl(self.rfile.read(length).decode()))
                status, body = google.answer(form)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.token_uri = f"http://127.0.0.1:{self.server.server_address[1]}/token"

    def answer(self, form: dict[str, str]) -> tuple[int, dict]:
        if form.get("client_secret") != SECRET:
            return 401, {"error": "invalid_client"}
        if form["grant_type"] == "authorization_code":
            digest = hashlib.sha256(form["code_verifier"].encode()).digest()
            pkce_ok = base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == self.challenge
            if form["code"] != "the-code" or not pkce_ok or form["redirect_uri"] != self.redirect:
                return 400, {"error": "invalid_grant"}
            return 200, {
                "access_token": "ya29.first",
                "expires_in": 3599,
                "refresh_token": REFRESH,
                "scope": " ".join(SCOPES),
            }
        if self.fail == "invalid_grant":
            return 400, {"error": "invalid_grant", "error_description": "Token has been revoked."}
        if self.fail == "server":
            return 503, {"error": "backend"}
        assert form["refresh_token"] == REFRESH
        self.refreshes += 1
        body = {"access_token": f"ya29.access-{self.refreshes}", "expires_in": 3600}
        if self.rotate:
            body["refresh_token"] = REFRESH
        return 200, body

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def google():
    fake = FakeGoogle()
    yield fake
    fake.close()


def client_for(google: FakeGoogle) -> OAuthClient:
    return OAuthClient("reel-client.apps.googleusercontent.com", SECRET, token_uri=google.token_uri)


def browser(google: FakeGoogle, *, answer: str = "code", favicon: bool = True):
    """Opens the sign-in address like a person: Google redirects back to Reel."""

    def open_url(url: str) -> None:
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        google.challenge, google.redirect = query["code_challenge"], query["redirect_uri"]
        assert query["code_challenge_method"] == "S256" and query["access_type"] == "offline"
        assert query["scope"].split() == list(SCOPES)
        assert query["redirect_uri"].startswith("http://127.0.0.1:")
        if favicon:
            with pytest.raises(urllib.error.HTTPError) as missing:
                urllib.request.urlopen(query["redirect_uri"] + "/favicon.ico", timeout=5)
            missing.value.close()
        values = {"code": "the-code", "state": query["state"]}
        if answer == "denied":
            values = {"error": "access_denied", "state": query["state"]}
        elif answer == "wrong-state":
            values["state"] = "someone-else"
        elif answer == "none":
            return
        target = query["redirect_uri"] + "/?" + urllib.parse.urlencode(values)
        with urllib.request.urlopen(target, timeout=5) as response:
            response.read()

    return open_url


def connected(google: FakeGoogle) -> MemorySecretStore:
    store = MemorySecretStore()
    authorize(client_for(google), store, open_url=browser(google))
    return store


def test_sign_in_keeps_the_refresh_token_and_reports_no_secret(google, caplog):
    store = MemorySecretStore()
    result = authorize(client_for(google), store, open_url=browser(google))
    record = json.loads(store.values[STORE_KEY])
    assert record["refresh_token"] == REFRESH and record["scopes"] == list(SCOPES)
    assert record["client_id"] == "reel-client.apps.googleusercontent.com"
    assert "refresh_token" not in result and result["missing_scopes"] == []
    assert connection(store)["scopes"] == list(SCOPES)
    assert (
        REFRESH not in caplog.text and SECRET not in caplog.text and "the-code" not in caplog.text
    )


@pytest.mark.parametrize(
    ("answer", "reason"), [("denied", "denied"), ("wrong-state", "state"), ("none", "timeout")]
)
def test_a_failed_sign_in_stores_nothing(google, answer, reason):
    store = MemorySecretStore()
    with pytest.raises(AuthError) as error:
        authorize(client_for(google), store, open_url=browser(google, answer=answer), timeout=1)
    assert error.value.reason == reason and store.values == {}


def test_access_tokens_are_refreshed_before_they_expire(google):
    store = connected(google)
    now = [1000.0]
    tokens = GoogleTokens(client_for(google), store, clock=lambda: now[0])
    assert tokens.token() == "ya29.access-1"
    assert tokens.token() == "ya29.access-1"  # cached
    now[0] += 3600 - 100  # less than two minutes left
    assert tokens.token() == "ya29.access-2"
    tokens.invalidate()  # after an HTTP 401
    assert tokens.token() == "ya29.access-3" and google.refreshes == 3


def test_a_revoked_grant_is_a_clear_error_without_retries(google):
    store = connected(google)
    google.fail = "invalid_grant"
    with pytest.raises(AuthError) as error:
        GoogleTokens(client_for(google), store).token()
    assert error.value.retryable is False and "run 'reel auth google' again" in str(error.value)
    assert REFRESH not in str(error.value)


def test_a_google_outage_is_retryable(google):
    store = connected(google)
    google.fail = "server"
    with pytest.raises(AuthError) as error:
        GoogleTokens(client_for(google), store).token()
    assert error.value.retryable is True


def test_not_connected_or_another_client(google):
    with pytest.raises(AuthError, match="not connected"):
        GoogleTokens(client_for(google), MemorySecretStore()).token()
    store = connected(google)
    other = OAuthClient("other.apps.googleusercontent.com", SECRET, token_uri=google.token_uri)
    with pytest.raises(AuthError, match="another OAuth client"):
        GoogleTokens(other, store).token()


def test_a_rotated_refresh_token_is_kept(google):
    store = connected(google)
    google.rotate = True
    GoogleTokens(client_for(google), store).token()
    assert json.loads(store.values[STORE_KEY])["refresh_token"] == REFRESH


def test_the_publishers_see_the_auth_error_itself():
    api = GoogleApiClient(
        GoogleTokens(OAuthClient("id", SECRET), MemorySecretStore()),
        base_url="https://www.googleapis.com/youtube/v3/",
    )
    with pytest.raises(AuthError) as error:
        api.request("GET", "channels", params={"mine": "true"})
    assert error.value.reason == "not_connected" and error.value.retryable is False


def test_the_client_file_must_be_a_desktop_client(tmp_path):
    path = tmp_path / "client.json"
    path.write_text(json.dumps({"installed": {"client_id": "x", "client_secret": SECRET}}))
    client = OAuthClient.from_file(path)
    assert client.token_uri == "https://oauth2.googleapis.com/token"
    assert SECRET not in repr(client)
    path.write_text(json.dumps({"web": {"client_id": "x", "client_secret": SECRET}}))
    with pytest.raises(AuthError, match="Desktop app"):
        OAuthClient.from_file(path)


def test_forget_removes_the_token(google):
    store = connected(google)
    assert forget(store) is True and forget(store) is False
    assert connection(store) is None


def test_the_file_store_is_for_development_and_warns(tmp_path, caplog):
    store = FileSecretStore(tmp_path / "data" / "secrets.dev.json")
    assert store.get("a") is None
    store.set("a", "value-1")
    assert store.get("a") == "value-1" and store.delete("a") and not store.delete("a")
    assert "development only" in caplog.text and "value-1" not in caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")
def test_windows_credential_manager_round_trip():
    store = WindowsCredentialStore(f"ReelTest-{uuid.uuid4().hex[:8]}")
    try:
        assert store.get(STORE_KEY) is None
        store.set(STORE_KEY, json.dumps({"refresh_token": REFRESH, "note": "Հայերեն"}))
        assert json.loads(store.get(STORE_KEY))["note"] == "Հայերեն"
        with pytest.raises(SecretStoreError, match="too large"):
            store.set("big", "x" * 3000)
    finally:
        store.delete(STORE_KEY)
    assert store.get(STORE_KEY) is None and store.delete(STORE_KEY) is False


def test_reel_auth_status_without_and_with_a_connection(project, write_config, capsys):
    write_config('[auth]\nstore = "file"\n')
    config = str(project / "config" / "reel.toml")
    assert main(["--config", config, "auth", "status"]) == 1
    assert "Not connected to Google (file store)" in capsys.readouterr().out
    FileSecretStore(project / "data" / "secrets.dev.json").set(
        STORE_KEY,
        json.dumps({"refresh_token": REFRESH, "client_id": "x", "scopes": list(SCOPES),
                    "connected_at": "2026-11-20T00:00:00+00:00"}),
    )  # fmt: skip
    assert main(["--config", config, "auth", "status"]) == 0
    out = capsys.readouterr().out
    assert "Connected since 2026-11-20" in out and "4 scopes" in out and REFRESH not in out
    assert main(["--config", config, "auth", "google"]) == 1  # no client file configured
    assert "GOOGLE_CLIENT_SECRETS_FILE is not set" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("extra", "asked"),
    [
        ([], list(SCOPES)),
        (["--only", "youtube"], list(SCOPES[:2])),
        (["--only", "classroom"], list(SCOPES[2:])),
    ],
)
def test_reel_auth_google_can_ask_for_one_service_only(project, write_config, monkeypatch, extra,
                                                        asked):  # fmt: skip
    """An account where Classroom is off is refused by Google for the whole request."""
    write_config('[auth]\nstore = "file"\n')
    client_file = project / "config" / "google-client.json"
    client_file.write_text(json.dumps({"installed": {"client_id": "id", "client_secret": "s"}}))
    (project / ".env").write_text(f"GOOGLE_CLIENT_SECRETS_FILE={client_file}\n")
    seen = {}

    def fake_authorize(client, store, *, open_url, scopes, port):
        seen["scopes"] = list(scopes)
        return {"scopes": list(scopes), "missing_scopes": []}

    monkeypatch.setattr("reel.auth.commands.authorize", fake_authorize)
    config = str(project / "config" / "reel.toml")
    assert main(["--config", config, "auth", "google", "--no-browser", *extra]) == 0
    assert seen["scopes"] == asked
