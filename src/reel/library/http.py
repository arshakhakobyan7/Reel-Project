"""Idempotent Academy Library PUT client using the standard library (T-705)."""

import json
import logging
import math
from http.client import HTTPException, HTTPMessage
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from reel.library.port import (
    MAX_JSON_BYTES,
    LibraryItem,
    LibraryPublisher,
    LibraryResult,
    PermanentLibraryError,
    RetryableLibraryError,
    _decode_json,
    _text,
    _url,
    external_key,
)

log = logging.getLogger("reel.library")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: BinaryIO, code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> None:
        return None


def _retry_after(value: str | None) -> float | None:
    try:
        seconds = float(value) if value is not None else math.nan
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


class HttpLibraryPublisher(LibraryPublisher):
    """PUT once per attempt; retries always overwrite the same namespaced resource."""

    name = "http"

    def __init__(
        self,
        base_url: str,
        *,
        instance_id: str,
        token: str | None,
        timeout_seconds: float = 15.0,
        dry_run: bool = False,
    ) -> None:
        external_key(instance_id, 1)  # Validate without inventing/changing the namespace.
        self._instance_id = instance_id
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._dry_run = dry_run
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def publish(self, job_id: int, item: LibraryItem) -> LibraryResult:
        key = external_key(self._instance_id, job_id)
        payload = item.to_dict()
        if self._dry_run:
            log.info("Dry-run HTTP library PUT prepared for job %s", job_id)
            return LibraryResult(None, None, False, key, dry_run=True, payload=payload)
        if (
            not self._token
            or not self._token.isascii()
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self._token)
        ):
            raise PermanentLibraryError("missing_token")
        if (
            not _url(self._base_url)
            or urlsplit(self._base_url).query
            or urlsplit(self._base_url).fragment
            or not math.isfinite(self._timeout)
            or self._timeout <= 0
        ):
            raise PermanentLibraryError("invalid_configuration")
        try:
            request = Request(
                self._base_url + "/entries/" + quote(key, safe=""),
                method="PUT",
                data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/json",
                },
            )
            with self._opener.open(request, timeout=self._timeout) as response:
                status = response.status
                if status not in {200, 201}:
                    raise ValueError("unexpected success status")
                if (
                    response.headers.get_content_type() != "application/json"
                    or response.headers.get_content_charset("utf-8").lower() != "utf-8"
                ):
                    raise ValueError("response is not UTF-8 JSON")
                result = _decode_json(response.read(MAX_JSON_BYTES + 1))
            if not isinstance(result, dict) or set(result) != {
                "external_key",
                "id",
                "url",
                "created",
                "item",
            }:
                raise ValueError("invalid response fields")
            if (
                result["external_key"] != key
                or not _text(result["id"], 200)
                or result["id"] != result["id"].strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in result["id"])
                or not _url(result["url"])
                or type(result["created"]) is not bool
                or result["created"] != (status == 201)
                or LibraryItem.from_dict(result["item"]).to_dict() != payload
            ):
                raise ValueError("response evidence does not match the request")
            return LibraryResult(
                result["id"], result["url"], result["created"], key, payload=payload
            )
        except HTTPError as error:
            with error:
                exception = (
                    RetryableLibraryError
                    if error.code == 429 or error.code == 408 or error.code >= 500
                    else PermanentLibraryError
                )
                raise exception(
                    f"http_{error.code}", retry_after=_retry_after(error.headers.get("Retry-After"))
                ) from None
        except URLError, OSError, HTTPException:
            raise RetryableLibraryError("connection_or_timeout") from None
        except ValueError, TypeError, RecursionError:
            raise RetryableLibraryError("invalid_response") from None
