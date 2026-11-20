"""Loopback-only, in-memory mock of the proposed Library contract (T-705)."""

import hmac
import json
import socket
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any, Self
from urllib.parse import unquote, urlsplit

from reel.library.port import _KEY, MAX_JSON_BYTES, LibraryItem, _decode_json


class _Server(ThreadingHTTPServer):
    daemon_threads = False


class LibraryMockServer:
    """Store one resource per external key and inject faults for tests/demos."""

    def __init__(
        self,
        *,
        token: str | None = None,
        status: int | None = None,
        delay_seconds: float = 0,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
        response_headers: dict[str, str] | None = None,
        drop_after_commit_once: bool = False,
    ) -> None:
        self._token = token
        self._status = status
        self._delay = delay_seconds
        self._raw_body = raw_body
        self._content_type = content_type
        self._headers = dict(response_headers or {})
        self._drop = drop_after_commit_once
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.request_count = 0
        self.entries: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []
        self.bodies: list[bytes] = []

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("library mock server is not running")
        return f"http://127.0.0.1:{self._server.server_port}"

    def start(self) -> Self:
        if self._server is not None:
            return self
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_PUT(self) -> None:
                owner._handle(self)

            def log_message(self, format: str, *args: Any) -> None:
                return  # Never log tokens, headers or request bodies.

        self._stop.clear()
        self._server = _Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()
        return self

    def close(self) -> None:
        if self._server is not None:
            self._stop.set()
            self._server.shutdown()
            self._thread.join()
            self._server.server_close()
            self._server = None
            self._thread = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _send(self, handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
        raw = (
            self._raw_body
            if self._raw_body is not None
            else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", self._content_type)
            handler.send_header("Content-Length", str(len(raw)))
            handler.send_header("Connection", "close")
            for key, value in self._headers.items():
                handler.send_header(key, value)
            handler.end_headers()
            handler.wfile.write(raw)
        except OSError:
            return  # Expected if a test deliberately times out/disconnects.

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        with self._lock:
            self.request_count += 1
        # Drain a bounded PUT body before early errors, so closing does not reset TCP
        # and turn a deliberate 401 response into a spurious connection failure.
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = handler.rfile.read(length) if 0 < length <= MAX_JSON_BYTES else b""
        if self._token is not None and not hmac.compare_digest(
            handler.headers.get("Authorization", "").encode(), f"Bearer {self._token}".encode()
        ):
            self._send(handler, 401, {"error": "unauthorized"})
            return
        parsed = urlsplit(handler.path)
        key = unquote(parsed.path.removeprefix("/entries/"))
        if (
            not parsed.path.startswith("/entries/")
            or not _KEY.fullmatch(key)
            or parsed.query
            or parsed.fragment
        ):
            self._send(handler, 400, {"error": "invalid_external_key"})
            return
        try:
            if not 0 < length <= MAX_JSON_BYTES:
                raise ValueError("invalid content length")
            if handler.headers.get_content_type() != "application/json":
                raise ValueError("request is not JSON")
            item = LibraryItem.from_dict(_decode_json(raw)).to_dict()
        except ValueError, TypeError, RecursionError:
            self._send(handler, 400, {"error": "invalid_item"})
            return
        self._stop.wait(self._delay)
        if self._status is not None:
            self._send(handler, self._status, {"error": "injected_fault"})
            return
        with self._lock:
            self.bodies.append(raw)
            self.requests.append({"external_key": key, "item": deepcopy(item)})
            created = key not in self.entries
            entry_id = self.entries[key]["id"] if not created else f"entry-{len(self.entries) + 1}"
            self.entries[key] = {"id": entry_id, "item": deepcopy(item)}
            drop, self._drop = self._drop, False
        if drop:
            handler.close_connection = True
            try:
                handler.connection.shutdown(socket.SHUT_RDWR)
                handler.connection.close()
            except OSError:
                return
            return
        self._send(
            handler,
            201 if created else 200,
            {
                "external_key": key,
                "id": entry_id,
                "url": self.url + "/entries/" + key,
                "created": created,
                "item": item,
            },
        )
