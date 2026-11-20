"""Loopback-only mock of the proposed Core Engine consent contract (T-601)."""

import hmac
import json
import threading
from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit


class _Server(ThreadingHTTPServer):
    daemon_threads = False  # server_close joins request threads, including timed-out clients.


class ConsentMockServer:
    """Serve synthetic group records; injectable faults support offline client tests."""

    def __init__(
        self,
        groups: Mapping[str, dict[str, Any]] | None = None,
        *,
        token: str | None = None,
        status: int = 200,
        delay_seconds: float = 0,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._groups = {
            name.casefold(): (name, deepcopy(record)) for name, record in (groups or {}).items()
        }
        self._token = token
        self._status = status
        self._delay = delay_seconds
        self._raw_body = raw_body
        self._content_type = content_type
        self._headers = dict(headers or {})
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._request_count = 0
        self.requests: list[dict[str, str]] = []  # Never store Authorization headers.

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("consent mock server is not running")
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def request_count(self) -> int:
        with self._lock:
            return self._request_count

    def start(self) -> Self:
        """Bind a free loopback port and start serving."""
        if self._server is not None:
            return self
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner._handle(self)

            def log_message(self, format: str, *args: Any) -> None:
                return  # Suppress request logging, including headers and query strings.

        self._stop.clear()
        self._server = _Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()
        return self

    def close(self) -> None:
        """Stop serving and join all server/request threads."""
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
        body = (
            self._raw_body
            if self._raw_body is not None
            else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", self._content_type)
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Connection", "close")
            for name, value in self._headers.items():
                handler.send_header(name, value)
            handler.end_headers()
            handler.wfile.write(body)
        except OSError:
            return  # A deliberate client timeout may close its socket before the response.

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        with self._lock:
            self._request_count += 1
        if self._token is not None and not hmac.compare_digest(
            handler.headers.get("Authorization", "").encode("utf-8"),
            f"Bearer {self._token}".encode(),
        ):
            self._send(handler, 401, {"error": "unauthorized"})
            return
        parsed = urlsplit(handler.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path != "/v1/consent":
            self._send(handler, 404, {"error": "endpoint_not_found"})
            return
        if not {"group"} <= query.keys() <= {"group", "lesson_date"} or any(
            len(values) != 1 or not values[0].strip() for values in query.values()
        ):
            self._send(handler, 400, {"error": "invalid_request"})
            return
        if "lesson_date" in query:
            try:
                if (
                    date.fromisoformat(query["lesson_date"][0]).isoformat()
                    != query["lesson_date"][0]
                ):
                    raise ValueError("date must use ISO format")
            except ValueError:
                self._send(handler, 400, {"error": "invalid_date"})
                return
        values = {key: items[0] for key, items in query.items()}
        with self._lock:
            self.requests.append(values)
        self._stop.wait(self._delay)
        if self._status != 200:
            self._send(handler, self._status, {"error": "injected_fault"})
            return
        record = self._groups.get(values["group"].casefold())
        if record is None:
            self._send(handler, 404, {"error": "group_not_found"})
            return
        name, entry = record
        self._send(
            handler,
            200,
            {
                "group": values["group"],
                "group_ref": entry.get("group_ref", name),
                "students": entry.get("students"),
            },
        )
