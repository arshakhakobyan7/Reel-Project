"""Passwords, signed session cookies, CSRF tokens and login throttling (T-802; NFR-5, D-049).

Only the Python standard library is used:

- Passwords: ``hashlib.scrypt`` (N=2^15, r=8, p=1, 16-byte salt, 64-byte key), stored as
  ``scrypt$<N>$<r>$<p>$<salt>$<key>`` (base64). Verification takes constant time.
- Sessions: the cookie holds a small JSON payload signed with HMAC-SHA256. It holds no
  secret: the user id, the issue and expiry times, the CSRF token and a fingerprint of the
  password hash. A changed password or a disabled account ends every session, because each
  request checks the account in the database.
- CSRF: one random token per session. Every form carries it, and every POST is checked.
- Login throttling: after 5 failed logins for one username or one client address within
  15 minutes, the login form refuses for 15 minutes.
"""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
_SCRYPT_KEY = 64
_SCRYPT_MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024
MIN_SECRET_LENGTH = 32


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class PasswordError(ValueError):
    """The password does not meet the rules."""


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordError(f"the password must have at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordError(f"the password must have at most {MAX_PASSWORD_LENGTH} characters")
    if not password.strip():
        raise PasswordError("the password must not be only spaces")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_SCRYPT_MAXMEM,
        dklen=_SCRYPT_KEY,
    )  # fmt: skip


def hash_password(password: str) -> str:
    check_password_rules(password)
    salt = secrets.token_bytes(16)
    key = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


# Verifying against this keeps the time of "no such user" equal to a wrong password.
_DUMMY_HASH = hash_password("reel-timing-equaliser-not-a-password")


def verify_password(password: str, stored: str | None) -> bool:
    """True if ``password`` matches ``stored``; a missing or malformed hash is False."""
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    candidate = stored or _DUMMY_HASH
    try:
        scheme, n, r, p, salt, key = candidate.split("$")
        if scheme != "scrypt":
            raise ValueError("unknown scheme")
        expected = _unb64(key)
        actual = _scrypt(password, _unb64(salt), int(n), int(r), int(p))
    except ValueError, binascii.Error, OverflowError, MemoryError:
        return False
    return hmac.compare_digest(actual, expected) and stored is not None


def password_fingerprint(stored: str) -> str:
    """A short value that changes when the password changes (kept in the session)."""
    return hashlib.sha256(stored.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Session:
    """The content of a session cookie. ``user_id`` is None before login."""

    csrf: str
    issued: float
    expires: float
    user_id: int | None = None
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"c": self.csrf, "i": self.issued, "e": self.expires, "u": self.user_id,
                "f": self.fingerprint}  # fmt: skip


class SessionSigner:
    """Signs and checks session cookies with HMAC-SHA256."""

    def __init__(self, secret: str, *, lifetime_seconds: float, clock: Callable[[], float]) -> None:
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"the session secret must have at least {MIN_SECRET_LENGTH} chars")
        self._key = hashlib.sha256(b"reel-session-v1\0" + secret.encode("utf-8")).digest()
        self.lifetime = lifetime_seconds
        self.clock = clock

    def new(self, *, user_id: int | None = None, fingerprint: str = "") -> Session:
        now = self.clock()
        return Session(secrets.token_urlsafe(32), now, now + self.lifetime, user_id, fingerprint)

    def dumps(self, session: Session) -> str:
        body = _b64(json.dumps(session.to_dict(), separators=(",", ":")).encode("utf-8"))
        signature = hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
        return f"{body}.{_b64(signature)}"

    def loads(self, value: str | None) -> Session | None:
        """The session in a cookie, or None when it is missing, forged or expired."""
        if not value or len(value) > 4096 or value.count(".") != 1:
            return None
        body, signature = value.split(".")
        expected = hmac.new(self._key, body.encode("ascii", "replace"), hashlib.sha256).digest()
        try:
            if not hmac.compare_digest(_unb64(signature), expected):
                return None
            data = json.loads(_unb64(body))
            session = Session(
                str(data["c"]), float(data["i"]), float(data["e"]),
                None if data["u"] is None else int(data["u"]), str(data["f"]),
            )  # fmt: skip
        except ValueError, KeyError, TypeError, binascii.Error:
            return None
        if not session.issued <= self.clock() < session.expires or len(session.csrf) < 32:
            return None
        return session


def csrf_matches(session: Session | None, token: str | None) -> bool:
    if session is None or not token:
        return False
    return hmac.compare_digest(session.csrf.encode("utf-8"), token.encode("utf-8", "replace"))


@dataclass
class LoginThrottle:
    """Counts failed logins per username and per client address (in memory)."""

    max_failures: int = 5
    window_seconds: float = 15 * 60
    clock: Callable[[], float] = time.monotonic
    _failures: dict[str, deque[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _recent(self, key: str, now: float) -> deque[float]:
        times = self._failures.setdefault(key, deque())
        while times and times[0] <= now - self.window_seconds:
            times.popleft()
        return times

    def blocked(self, *keys: str) -> bool:
        with self._lock:
            now = self.clock()
            return any(len(self._recent(key, now)) >= self.max_failures for key in keys)

    def failed(self, *keys: str) -> None:
        with self._lock:
            now = self.clock()
            for key in keys:
                self._recent(key, now).append(now)
            if len(self._failures) > 10_000:  # forget idle keys so memory stays small
                for key in [k for k, v in self._failures.items() if not v]:
                    del self._failures[key]

    def succeeded(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)
