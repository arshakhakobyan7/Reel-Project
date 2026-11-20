"""Small authenticated Google JSON/media HTTP client and protected resumable state (T-702)."""

import base64
import ctypes
import json
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.client import HTTPException, HTTPMessage
from typing import Any, BinaryIO, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from reel.logging_setup import register_secret

_MAX_RESPONSE = 4 * 1024 * 1024
_TRANSIENT = frozenset(
    {
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "backendError",
        "quotaExceeded",
        "uploadLimitExceeded",
    }
)
_REASONS = _TRANSIENT | frozenset(
    {
        "forbidden",
        "insufficientPermissions",
        "authError",
        "invalidCredentials",
        "badRequest",
        "invalidValue",
        "notFound",
        "videoNotFound",
        "playlistNotFound",
        "playlistItemNotFound",
        "uploadNotFound",
        "invalidTitle",
        "invalidDescription",
        "invalidCategoryId",
        "youtubeSignupRequired",
        "channelNotFound",
        "accountClosed",
        "accountSuspended",
        "invalidVideoMetadata",
        "mediaBodyRequired",
        "duplicate",
    }
)


class AccessTokens(Protocol):
    """T-701 supplies valid access tokens and refreshes after invalidation."""

    def token(self) -> str: ...

    def invalidate(self) -> None: ...


class GoogleApiError(RuntimeError):
    """Sanitised failure whose retryability the caller can use for scheduling."""

    def __init__(self, message: str, *, status: int | None, reason: str, retryable: bool) -> None:
        self.status = status
        self.reason = reason
        self.retryable = retryable
        super().__init__(message)


def _error(status: int, body: bytes) -> GoogleApiError:
    reasons: list[str] = []
    try:
        error = json.loads(body).get("error", {})
        if isinstance(error, dict):
            reasons = [
                item.get("reason", "") for item in error.get("errors", []) if isinstance(item, dict)
            ]
    except ValueError, AttributeError, TypeError:
        reasons = []
    # Never copy Google's free-text message or an unrecognised reason into an exception.
    reason = next((r for r in reasons if isinstance(r, str) and r in _TRANSIENT), "")
    reason = reason or next(
        (r for r in reasons if isinstance(r, str) and r in _REASONS), f"http_{status}"
    )
    quota = reason in {"quotaExceeded", "uploadLimitExceeded"}
    message = f"Google API request failed ({status}, {reason})."
    if quota:
        message += " The daily quota is used up; retry after the quota resets."
    return GoogleApiError(
        message,
        status=status,
        reason=reason,
        retryable=status >= 500 or status == 429 or reason in _TRANSIENT,
    )


def _invalid(message: str, reason: str = "invalid_configuration") -> GoogleApiError:
    return GoogleApiError(message, status=None, reason=reason, retryable=False)


def _origin(url: str) -> tuple[str, str, int]:
    try:
        parts = urlsplit(url)
        valid = (
            (parts.scheme == "https" or (parts.scheme == "http" and parts.hostname == "127.0.0.1"))
            and parts.hostname
            and parts.username is None
            and parts.password is None
            and not parts.fragment
            and not any(ord(c) <= 32 or ord(c) == 127 for c in url)
        )
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if valid and parts.port != 0:
            return parts.scheme, parts.hostname, port
    except ValueError:
        valid = False
    raise _invalid("Invalid Google API endpoint.")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: BinaryIO, code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> None:
        return None


@dataclass(frozen=True)
class GoogleResponse:
    status: int
    headers: dict[str, str] = field(repr=False)
    data: bytes = field(repr=False)

    def json(self) -> dict[str, Any]:
        """Decode an object response without exposing untrusted response text on errors."""
        try:
            result = json.loads(self.data)
            if isinstance(result, dict):
                return result
        except ValueError, RecursionError:
            result = None
        raise GoogleApiError(
            "Google API returned invalid JSON.",
            status=self.status,
            reason="invalid_response",
            retryable=True,
        )


class GoogleApiClient:
    """Bearer-authenticated requests; exactly one refresh/retry on HTTP 401."""

    def __init__(
        self,
        tokens: AccessTokens,
        *,
        base_url: str,
        allowed_urls: tuple[str, ...] = (),
        timeout_seconds: float = 30,
    ) -> None:
        self._tokens = tokens
        self._base = base_url.rstrip("/") + "/"
        self._origins = {_origin(url) for url in (self._base, *allowed_urls)}
        if urlsplit(self._base).query or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise _invalid("Invalid Google API client configuration.")
        self._timeout = timeout_seconds
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        accepted: tuple[int, ...] = (200, 201, 204),
        check: Callable[[], None] | None = None,
    ) -> GoogleResponse:
        url = path if "://" in path else self._base + path.lstrip("/")
        if _origin(url) not in self._origins:
            raise _invalid("Google API request has an untrusted origin.")
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        if data is not None and not isinstance(data, bytes):
            raise _invalid("Raw Google API data must be bytes.", "invalid_payload")
        request_headers = dict(headers or {})
        if any(key.lower() == "authorization" for key in request_headers):
            raise _invalid("Authentication headers are managed by GoogleApiClient.")
        if json_body is not None:
            if data is not None:
                raise _invalid("Choose a JSON or a raw request body.", "invalid_payload")
            try:
                data = json.dumps(json_body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except TypeError, ValueError, UnicodeError:
                raise _invalid("Invalid Google API JSON body.", "invalid_payload") from None
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        request_headers["Accept"] = "application/json"
        for attempt in range(2):
            if check is not None:
                check()
            try:
                token = self._tokens.token()
            except GoogleApiError:
                raise  # e.g. reel.auth.google.AuthError: its own message and retryability
            except Exception:
                raise _invalid("Cannot obtain a Google access token.", "authentication") from None
            if (
                not isinstance(token, str)
                or not token
                or not token.isascii()
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token)
            ):
                raise _invalid("A valid Google access token is required.", "authentication")
            register_secret(token)
            request_headers["Authorization"] = f"Bearer {token}"
            try:
                request = Request(url, data=data, headers=request_headers, method=method)
                try:
                    response = self._opener.open(request, timeout=self._timeout)
                except HTTPError as error:
                    response = (
                        error  # Includes 308; never follow a resumable response as a redirect.
                    )
                with response:
                    status = response.code
                    body = response.read(_MAX_RESPONSE + 1)
                    response_headers = {k.lower(): v for k, v in response.headers.items()}
                if len(body) > _MAX_RESPONSE:
                    raise GoogleApiError(
                        "Google API response is too large.",
                        status=status,
                        reason="invalid_response",
                        retryable=True,
                    )
            except URLError, OSError, HTTPException:
                raise GoogleApiError(
                    "Google API connection failed or timed out.",
                    status=None,
                    reason="connection_error",
                    retryable=True,
                ) from None
            except ValueError, UnicodeError:
                raise _invalid("Invalid Google API request.", "invalid_payload") from None
            if status == 401 and attempt == 0:
                try:
                    self._tokens.invalidate()
                except Exception:
                    raise _invalid(
                        "Cannot refresh the Google access token.", "authentication"
                    ) from None
                continue
            if status not in accepted:
                raise _error(status, body)
            return GoogleResponse(status, response_headers, body)
        raise _invalid("Google authentication failed.", "authentication")  # Defensive bound.


class SessionCodec(Protocol):
    """Protect session URLs before they enter persisted publication details."""

    def seal(self, url: str) -> str: ...

    def unseal(self, protected: str) -> str: ...


class WindowsSessionCodec:
    """Protect upload session URLs with current-user Windows DPAPI, without dependencies."""

    def _transform(self, data: bytes, *, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise _invalid(
                "This platform needs an injected secure SessionCodec for resumable state."
            )

        class Blob(ctypes.Structure):
            _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        source_buffer = ctypes.create_string_buffer(data)
        source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        description_type = ctypes.c_void_p if decrypt else ctypes.c_wchar_p
        operation.argtypes = [
            ctypes.POINTER(Blob),
            description_type,
            ctypes.POINTER(Blob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(Blob),
        ]
        operation.restype = ctypes.c_int
        # CRYPTPROTECT_UI_FORBIDDEN; protection is bound to this OS user, not machine-wide.
        if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise _invalid(
                "Cannot protect or restore resumable upload state.", "invalid_session_state"
            )
        try:
            return ctypes.string_at(target.data, target.size)
        finally:
            kernel.LocalFree(target.data)

    def seal(self, url: str) -> str:
        return "dpapi:" + base64.b64encode(
            self._transform(url.encode("utf-8"), decrypt=False)
        ).decode("ascii")

    def unseal(self, protected: str) -> str:
        try:
            if not protected.startswith("dpapi:"):
                raise ValueError("invalid codec")
            raw = base64.b64decode(protected[6:], validate=True)
            return self._transform(raw, decrypt=True).decode("utf-8")
        except ValueError, UnicodeError:
            raise _invalid(
                "Cannot restore resumable upload state.", "invalid_session_state"
            ) from None
