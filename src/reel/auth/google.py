"""Google OAuth for YouTube and Classroom (T-701; NFR-5, R-7, D-047).

``reel auth google`` runs Google's installed-app flow: a one-time browser sign-in, answered on a
local loopback address (``http://127.0.0.1:<port>``) with PKCE and a random ``state``. The
refresh token is kept in the secret store (Windows Credential Manager), never in a file of the
project. ``GoogleTokens`` gives the publishers a fresh access token and refreshes it before it
expires; a revoked or expired grant gives a clear error (re-run ``reel auth google``) that fails
the stage without retries, so staff get a notification.
"""

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

from reel.auth.store import SecretStore, SecretStoreError
from reel.logging_setup import register_secret
from reel.publish.google_api import GoogleApiError

if TYPE_CHECKING:
    from reel.config.loader import LoadedConfig

log = logging.getLogger("reel.auth")

SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",  # videos.insert
    "https://www.googleapis.com/auth/youtube",  # list/update videos, playlists, thumbnails
    "https://www.googleapis.com/auth/classroom.announcements",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials",
)
# Access asked per service (reel auth google --only). An account where one service is
# switched off (e.g. no Classroom) is refused by Google for the whole request with
# "This service isn't available for your account", so such an account asks for YouTube only.
SERVICE_SCOPES = {"youtube": SCOPES[:2], "classroom": SCOPES[2:]}
STORE_KEY = "google/oauth"
REFRESH_MARGIN = 120  # seconds before expiry when a new access token is fetched
TIMEOUT = 30


class AuthError(GoogleApiError):
    """Google access cannot be obtained. ``retryable`` is False when staff must act."""

    def __init__(self, message: str, *, reason: str, retryable: bool = False) -> None:
        super().__init__(message, status=None, reason=reason, retryable=retryable)


NOT_CONNECTED = (
    "Reel is not connected to Google; run 'reel auth google' (see docs/setup/google.md)."
)
REVOKED = (
    "Google access was revoked or has expired; run 'reel auth google' again "
    "(an app in 'Testing' status loses access after 7 days, see docs/setup/google.md)."
)


@dataclass(frozen=True)
class OAuthClient:
    """The "Desktop app" OAuth client from Google Cloud (its JSON file)."""

    client_id: str
    client_secret: str
    auth_uri: str = "https://accounts.google.com/o/oauth2/v2/auth"
    token_uri: str = "https://oauth2.googleapis.com/token"

    def __repr__(self) -> str:
        return f"OAuthClient(client_id={self.client_id!r})"

    @classmethod
    def from_file(cls, path: Path | str) -> OAuthClient:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AuthError(
                f"cannot read the Google client file {Path(path).name}: {type(exc).__name__}",
                reason="client_file",
            ) from None
        installed = data.get("installed") if isinstance(data, dict) else None
        if not isinstance(installed, dict):
            raise AuthError(
                "the Google client file must be for a 'Desktop app' client ('installed')",
                reason="client_file",
            )
        values = {key: installed.get(key) for key in ("client_id", "client_secret")}
        if not all(isinstance(v, str) and v for v in values.values()):
            raise AuthError("the Google client file has no client_id/client_secret",
                            reason="client_file")  # fmt: skip
        register_secret(values["client_secret"])
        extra = {
            key: installed[key]
            for key in ("auth_uri", "token_uri")
            if isinstance(installed.get(key), str) and installed[key].startswith("https://")
        }
        return cls(values["client_id"], values["client_secret"], **extra)


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


def _post(url: str, fields: dict[str, str], timeout: float) -> dict[str, Any]:
    """POST a form to Google's token endpoint; errors without any secret in the message."""
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(1_000_000))
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read(100_000)).get("error", "")
        except ValueError, AttributeError, OSError:
            error = ""
        finally:
            exc.close()
        error = error if isinstance(error, str) and error.isascii() and len(error) < 60 else ""
        if error == "invalid_grant":
            raise AuthError(REVOKED, reason="invalid_grant") from None
        if error in {"invalid_client", "unauthorized_client"}:
            raise AuthError(
                "Google rejected the OAuth client; check GOOGLE_CLIENT_SECRETS_FILE",
                reason=error,
            ) from None
        raise AuthError(
            f"Google token endpoint answered HTTP {exc.code} {error}".strip(),
            reason=error or "http_error",
            retryable=exc.code >= 500 or exc.code == 429,
        ) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise AuthError(
            f"cannot reach Google's token endpoint ({type(exc).__name__})",
            reason="unreachable",
            retryable=True,
        ) from None


class _Callback(HTTPServer):
    result: dict[str, str] | None = None


def authorize(
    client: OAuthClient,
    store: SecretStore,
    *,
    open_url: Callable[[str], Any],
    scopes: tuple[str, ...] = SCOPES,
    port: int = 0,
    timeout: float = 300,
) -> dict[str, Any]:
    """Run the browser sign-in once and keep the refresh token. ``open_url`` opens (or prints)
    the sign-in address. Returns what was stored, without the token."""
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            values = {key: items[0] for key, items in query.items() if items}
            if "code" not in values and "error" not in values:
                self.send_response(404)
                self.end_headers()
                return
            self.server.result = values  # type: ignore[attr-defined]
            ok = "code" in values and values.get("state") == state
            body = (
                "Reel-ը միացված է Google-ին։ Կարող եք փակել այս էջը։ / Reel is connected."
                if ok
                else "Մուտքը չհաջողվեց։ / Sign-in failed."
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # no URLs with codes in logs
            pass

    server = _Callback(("127.0.0.1", port), Handler)
    redirect = f"http://127.0.0.1:{server.server_address[1]}"
    url = (
        client.auth_uri
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client.client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": " ".join(scopes),
                "access_type": "offline",  # a refresh token
                "prompt": "consent",  # a refresh token also on a second sign-in
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    deadline = time.monotonic() + timeout

    def serve() -> None:  # other requests (e.g. /favicon.ico) do not end the wait
        while server.result is None and time.monotonic() < deadline:
            server.timeout = max(0.1, min(1.0, deadline - time.monotonic()))
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    try:
        thread.start()
        open_url(url)
        thread.join(timeout + 5)
    finally:
        server.server_close()
    result = server.result
    if result is None:
        raise AuthError("no answer from the browser sign-in in time", reason="timeout")
    if result.get("state") != state:
        raise AuthError("the sign-in answer does not belong to this request", reason="state")
    if "error" in result:
        raise AuthError(f"Google sign-in was not completed: {result['error'][:60]}",
                        reason="denied")  # fmt: skip
    register_secret(result["code"])
    answer = _post(
        client.token_uri,
        {
            "code": result["code"],
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        TIMEOUT,
    )
    refresh = answer.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise AuthError("Google did not return a refresh token", reason="no_refresh_token")
    register_secret(refresh, answer.get("access_token"))
    granted = str(answer.get("scope", ""))
    missing = [scope for scope in scopes if scope not in granted.split()]
    record = {
        "refresh_token": refresh,
        "client_id": client.client_id,
        "scopes": granted.split(),
        "connected_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    try:
        store.set(STORE_KEY, json.dumps(record))
    except SecretStoreError as exc:
        raise AuthError(f"cannot keep the token: {exc}", reason="store") from None
    log.info("connected to Google (%d scopes)", len(record["scopes"]))
    return {**{k: v for k, v in record.items() if k != "refresh_token"}, "missing_scopes": missing}


def connection(store: SecretStore) -> dict[str, Any] | None:
    """What is stored (without the token), or None when not connected."""
    record = _record(store)
    if record is None:
        return None
    return {k: v for k, v in record.items() if k != "refresh_token"}


def _record(store: SecretStore) -> dict[str, Any] | None:
    try:
        raw = store.get(STORE_KEY)
    except SecretStoreError as exc:
        raise AuthError(f"cannot read the token store: {exc}", reason="store") from None
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        raise AuthError("the stored Google token is damaged; run 'reel auth google' again",
                        reason="store") from None  # fmt: skip
    if not isinstance(record, dict) or not isinstance(record.get("refresh_token"), str):
        raise AuthError("the stored Google token is damaged; run 'reel auth google' again",
                        reason="store")  # fmt: skip
    register_secret(record["refresh_token"])
    return record


class GoogleTokens:
    """Fresh access tokens for the publishers (``reel.publish.google_api.AccessTokens``)."""

    def __init__(
        self,
        client: OAuthClient,
        store: SecretStore,
        *,
        clock: Callable[[], float] = time.time,
        timeout: float = TIMEOUT,
    ) -> None:
        self.client, self.store, self.clock, self.timeout = client, store, clock, timeout
        self._lock = threading.Lock()
        self._access: str | None = None
        self._expires = 0.0

    def token(self) -> str:
        with self._lock:
            if self._access and self.clock() < self._expires - REFRESH_MARGIN:
                return self._access
            record = _record(self.store)
            if record is None:
                raise AuthError(NOT_CONNECTED, reason="not_connected")
            if record.get("client_id") != self.client.client_id:
                raise AuthError(
                    "the stored Google token belongs to another OAuth client; "
                    "run 'reel auth google' again",
                    reason="client_changed",
                )
            answer = _post(
                self.client.token_uri,
                {
                    "client_id": self.client.client_id,
                    "client_secret": self.client.client_secret,
                    "refresh_token": record["refresh_token"],
                    "grant_type": "refresh_token",
                },
                self.timeout,
            )
            access = answer.get("access_token")
            if not isinstance(access, str) or not access:
                raise AuthError("Google returned no access token", reason="no_access_token",
                                retryable=True)  # fmt: skip
            register_secret(access)
            expires_in = answer.get("expires_in")
            lifetime = float(expires_in) if isinstance(expires_in, int | float) else 3600.0
            self._access, self._expires = access, self.clock() + lifetime
            rotated = answer.get("refresh_token")
            if isinstance(rotated, str) and rotated and rotated != record["refresh_token"]:
                register_secret(rotated)
                self.store.set(STORE_KEY, json.dumps({**record, "refresh_token": rotated}))
            return access

    def invalidate(self) -> None:
        """After HTTP 401: the next ``token()`` fetches a new access token."""
        with self._lock:
            self._access, self._expires = None, 0.0


def forget(store: SecretStore) -> bool:
    """Remove the stored token (Google access can also be removed in the Google account)."""
    return store.delete(STORE_KEY)


def make_google_tokens(cfg: LoadedConfig, store: SecretStore | None = None) -> GoogleTokens:
    """Tokens for the configured OAuth client (``GOOGLE_CLIENT_SECRETS_FILE``) and store."""
    from reel.auth.store import make_secret_store

    path = cfg.secrets.get("GOOGLE_CLIENT_SECRETS_FILE")
    if not path:
        raise AuthError(
            "GOOGLE_CLIENT_SECRETS_FILE is not set (.env); see docs/setup/google.md",
            reason="not_configured",
        )
    return GoogleTokens(OAuthClient.from_file(cfg.resolve(path)), store or make_secret_store(cfg))
