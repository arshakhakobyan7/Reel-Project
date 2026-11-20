"""Core Engine JSON consent client using urllib only (T-601)."""

import logging
import math
from datetime import date
from http.client import HTTPException, HTTPMessage
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from reel.consent.provider import (
    MAX_JSON_BYTES,
    ConsentDecision,
    ConsentProvider,
    _decision,
    _decode_json,
    _identifier,
    _parse_record,
)
from reel.db.enums import ConsentResult

log = logging.getLogger("reel.consent")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: BinaryIO, code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> None:
        return None  # Credentials must never be forwarded to another URL.


def _response_json(response: Any) -> Any:
    if response.headers.get_content_type() != "application/json":
        raise ValueError("response is not JSON")
    if response.headers.get_content_charset("utf-8").lower() != "utf-8":
        raise ValueError("response is not UTF-8")
    return _decode_json(response.read(MAX_JSON_BYTES + 1))


class HttpConsentProvider(ConsentProvider):
    """Treat auth errors and all unexpected HTTP/JSON results as unreachable.

    A structured 404 group_not_found is unknown. Valid student evidence is mapped by
    D-019, never by a server-supplied approval flag. Redirects/proxies are disabled.
    """

    name = "http"

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None,
        timeout_seconds: float = 15.0,
        dry_run: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._dry_run = dry_run
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def check(self, group: str, lesson_date: date | None) -> ConsentDecision:
        """GET /v1/consent with an optional ISO lesson date; expose no server error bodies."""

        def unavailable(message: str) -> ConsentDecision:
            return ConsentDecision(ConsentResult.UNREACHABLE, None, (), message)

        group = group.strip()
        if not _identifier(group):
            return ConsentDecision(ConsentResult.UNKNOWN, None, (), "No valid group was supplied.")
        try:
            parts = urlsplit(self._base_url)
            valid_url = (
                parts.scheme in {"http", "https"}
                and parts.hostname
                and parts.port != 0
                and parts.username is None
                and parts.password is None
                and not parts.query
                and not parts.fragment
                and not any(ord(char) <= 32 or ord(char) == 127 for char in self._base_url)
            )
        except ValueError:
            valid_url = False
        if not valid_url or not math.isfinite(self._timeout) or self._timeout <= 0:
            return unavailable("Consent HTTP endpoint or timeout configuration is invalid.")
        query = {"group": group}
        if lesson_date is not None:
            query["lesson_date"] = lesson_date.isoformat()
        path = "/v1/consent?" + urlencode(query)
        if self._dry_run:
            log.info("Dry-run consent request: GET %s", path)
            return unavailable(f"Dry-run: GET {path} was not sent; consent remains unverified.")
        if (
            not self._token
            or not self._token.isascii()
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self._token)
        ):
            return unavailable("CORE_ENGINE_API_TOKEN is missing or invalid.")
        try:
            request = Request(
                self._base_url + path,
                headers={"Accept": "application/json", "Authorization": f"Bearer {self._token}"},
                method="GET",
            )
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status != 200:
                    return unavailable(f"Unexpected consent HTTP status {response.status}.")
                payload = _response_json(response)
            if not isinstance(payload, dict) or set(payload) != {"group", "group_ref", "students"}:
                raise ValueError("invalid consent response")
            if payload["group"] != group:
                raise ValueError("response belongs to another group")
            record = {key: payload[key] for key in ("group_ref", "students")}
            return _decision(*_parse_record(record))
        except HTTPError as error:
            with error:
                if error.code == 404:
                    try:
                        if _response_json(error) == {"error": "group_not_found"}:
                            return ConsentDecision(
                                ConsentResult.UNKNOWN,
                                None,
                                (),
                                "The group is not known to Core Engine.",
                            )
                    except ValueError, OSError, HTTPException, RecursionError:
                        return unavailable("Consent service returned an invalid 404 response.")
                if error.code in {401, 403}:
                    return unavailable(
                        f"Consent service authentication was rejected (HTTP {error.code})."
                    )
                return unavailable(f"Consent service returned HTTP {error.code}.")
        except URLError, OSError, HTTPException:
            return unavailable("Consent service could not be reached or the request timed out.")
        except ValueError, RecursionError:
            return unavailable("Consent service returned malformed or unsupported JSON evidence.")
