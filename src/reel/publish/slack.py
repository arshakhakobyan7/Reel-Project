"""Slack publishing with metadata recovery and no database access (T-703).

External ids are ``channel:ts``. Retries must use the same channel/job/instance identity.
History failures never trigger a blind new post. The caller serialises attempts for a
job and persists the returned id; Slack does not provide an atomic history-and-post API.
"""

import json
import logging
import math
import re
import string
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as Date
from http.client import HTTPException, HTTPMessage
from typing import TYPE_CHECKING, Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from reel.config.schema import POST_FIELDS, SlackSettings, check_template

if TYPE_CHECKING:
    from reel.config.loader import LoadedConfig

log = logging.getLogger("reel.publish.slack")

_TRANSIENT = frozenset(
    {
        "ratelimited",
        "rate_limited",
        "internal_error",
        "fatal_error",
        "service_unavailable",
        "request_timeout",
        "timeout",
        "temporarily_unavailable",
    }
)
_PERMANENT = frozenset(
    {
        "invalid_auth",
        "not_authed",
        "token_revoked",
        "account_inactive",
        "channel_not_found",
        "not_in_channel",
        "is_archived",
        "no_permission",
        "missing_scope",
        "cant_update_message",
        "cannot_update_message",
        "message_not_found",
        "edit_window_closed",
        "invalid_arguments",
        "invalid_arg_name",
        "invalid_metadata_format",
        "invalid_metadata_schema",
        "metadata_too_large",
        "msg_too_long",
        "no_text",
        "not_allowed_token_type",
        "restricted_action",
        "access_denied",
        "team_access_not_granted",
        "invalid_post_type",
        "invalid_charset",
        "org_login_required",
    }
)
_TS = re.compile(r"[0-9]+\.[0-9]+\Z")
_CHANNEL_ID = re.compile(r"[CGD][A-Z0-9]{8,}\Z")
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_METHODS = frozenset(
    {"chat.postMessage", "chat.update", "chat.getPermalink", "conversations.history"}
)


class SlackError(RuntimeError):
    """A sanitised failure; the stage controls retry scheduling using these attributes."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        retry_after: float | None = None,
        external_id: str | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        self.external_id = external_id
        if code == "missing_token":
            message = "SLACK_BOT_TOKEN is required and must be valid in live mode."
        elif code == "invalid_channel":
            message = (
                "Use the Slack channel ID (e.g. C0123456789), "
                "not the channel name (e.g. #python-12)."
            )
        else:
            message = f"Slack request failed: {code}."
        super().__init__(message)


def _validate_channel(channel: Any) -> None:
    if not isinstance(channel, str) or not _CHANNEL_ID.fullmatch(channel):
        raise SlackError("invalid_channel", retryable=False)


def _retry_after(value: str | None) -> float | None:
    try:
        seconds = float(value) if value is not None else math.nan
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: BinaryIO, code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> None:
        return None  # Never forward the bearer token to another endpoint.


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("nonfinite JSON number")


class SlackClient:
    """Thin POST Web API client with JSON writes and form-encoded read methods."""

    def __init__(
        self,
        token: str | None,
        *,
        base_url: str = "https://slack.com/api/",
        timeout_seconds: float = 15.0,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/") + "/"
        self._timeout = timeout_seconds
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _call(self, method: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if method not in _METHODS:
            raise SlackError("invalid_method", retryable=False)
        _validate_channel(payload.get("channel"))
        if (
            not self._token
            or not self._token.isascii()
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self._token)
        ):
            raise SlackError("missing_token", retryable=False)
        try:
            parts = urlsplit(self._base_url)
            valid = (
                (
                    parts.scheme == "https"
                    or (parts.scheme == "http" and parts.hostname == "127.0.0.1")
                )
                and parts.hostname
                and parts.port != 0
                and not parts.query
                and not parts.fragment
                and parts.username is None
                and parts.password is None
                and not any(ord(char) <= 32 or ord(char) == 127 for char in self._base_url)
            )
        except ValueError:
            valid = False
        if not valid or not math.isfinite(self._timeout) or self._timeout <= 0:
            raise SlackError("invalid_configuration", retryable=False)
        try:
            if method in {"chat.postMessage", "chat.update"}:
                data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            else:
                fields = {
                    key: str(value).lower() if isinstance(value, bool) else str(value)
                    for key, value in payload.items()
                }
                data = urlencode(fields, encoding="utf-8").encode("ascii")
                content_type = "application/x-www-form-urlencoded"
            request = Request(
                self._base_url + method,
                data=data,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": content_type,
                    "Accept": "application/json",
                },
            )
        except TypeError, ValueError:
            raise SlackError("invalid_payload", retryable=False) from None
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status != 200:
                    raise SlackError(f"http_{response.status}", retryable=response.status >= 500)
                retry_after = _retry_after(response.headers.get("Retry-After"))
                if response.headers.get_content_type() != "application/json":
                    raise ValueError("not JSON")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise ValueError("response too large")
                result = json.loads(
                    raw.decode("utf-8"), object_pairs_hook=_json_object, parse_constant=_constant
                )
        except HTTPError as error:
            with error:
                raise SlackError(
                    "ratelimited" if error.code == 429 else f"http_{error.code}",
                    retryable=error.code == 429 or error.code >= 500,
                    retry_after=_retry_after(error.headers.get("Retry-After")),
                ) from None
        except URLError, OSError, HTTPException:
            raise SlackError("connection_error", retryable=True) from None
        except ValueError, RecursionError:
            raise SlackError("invalid_response", retryable=True) from None
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            raise SlackError("invalid_response", retryable=True)
        if result["ok"] is False:
            code = result.get("error")
            if not isinstance(code, str) or code not in _TRANSIENT | _PERMANENT:
                code = "api_error"
            raise SlackError(code, retryable=code in _TRANSIENT, retry_after=retry_after)
        return result

    def post_message(self, channel: str, text: str, metadata: dict[str, Any]) -> dict[str, Any]:
        """Post a message with its recovery metadata."""
        return self._call(
            "chat.postMessage",
            {
                "channel": channel,
                "text": text,
                "metadata": metadata,
                "parse": "none",
                "unfurl_links": False,
                "unfurl_media": False,
            },
        )

    def update_message(
        self, channel: str, ts: str, text: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """Update the existing message and preserve recovery metadata."""
        return self._call(
            "chat.update",
            {
                "channel": channel,
                "ts": ts,
                "text": text,
                "metadata": metadata,
                "parse": "none",
            },
        )

    def get_permalink(self, channel: str, ts: str) -> str:
        """Return Slack's permalink using a form-encoded POST body."""
        result = self._call("chat.getPermalink", {"channel": channel, "message_ts": ts})
        value = result.get("permalink")
        try:
            parts = urlsplit(value) if isinstance(value, str) else None
            valid = (
                parts is not None
                and parts.scheme == "https"
                and parts.hostname
                and parts.username is None
                and parts.password is None
                and not any(ord(char) <= 32 for char in value)
            )
        except ValueError:
            valid = False
        if not valid or result.get("channel", channel) != channel:
            raise SlackError("invalid_response", retryable=True)
        return value

    def history(self, channel: str, *, cursor: str = "", limit: int = 100) -> dict[str, Any]:
        """Read a history page with complete message metadata."""
        payload = {"channel": channel, "include_all_metadata": True, "limit": limit}
        if cursor:
            payload["cursor"] = cursor
        return self._call("conversations.history", payload)


@dataclass(frozen=True)
class SlackPost:
    """Publication input; the stage resolves the group's configured channel."""

    job_id: int
    channel: str
    title: str
    url: str
    summary: str = ""
    group: str = ""
    lesson: int | str = ""
    date: Date | str = ""
    duration: float | str = ""
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.job_id) is not int or self.job_id <= 0:
            raise SlackError("invalid_job_id", retryable=False)

    def metadata(self) -> dict[str, Any]:
        """Include the optional DB instance namespace required by D-027."""
        payload: dict[str, Any] = {"job_id": self.job_id}
        if self.instance_id is not None:
            payload["instance_id"] = self.instance_id
        return {"event_type": "reel_video", "event_payload": payload}


@dataclass(frozen=True)
class SlackResult:
    """The caller stores this result in PublicationTarget, including dry_run."""

    external_id: str | None
    url: str | None
    text: str
    created: bool
    dry_run: bool

    @property
    def updated(self) -> bool:
        return not self.dry_run and self.external_id is not None and not self.created


def render_message(template: str, post: SlackPost) -> str:
    """Format fields, then escape Slack's &, < and > without changing literal markup."""
    fields = {name: getattr(post, name) for name in POST_FIELDS}
    formatter = string.Formatter()
    rendered = []
    try:
        check_template(template, POST_FIELDS)
        for literal, field, spec, conversion in formatter.parse(template):
            rendered.append(literal)
            if field is None:
                continue
            if field not in POST_FIELDS or "{" in spec or "}" in spec:
                raise ValueError("only simple post fields are supported")
            value = fields[field]
            if conversion:
                value = formatter.convert_field(value, conversion)
            text = format(value, spec)
            rendered.append(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    except ValueError, TypeError, KeyError, AttributeError:
        raise SlackError("invalid_template", retryable=False) from None
    text = "".join(rendered)
    if not text.strip():
        raise SlackError("empty_message", retryable=False)
    return text


def _external_id(channel: str, ts: Any) -> str:
    if not isinstance(ts, str) or not _TS.fullmatch(ts):
        raise SlackError("invalid_response", retryable=True)
    return f"{channel}:{ts}"


class SlackPublisher:
    """Recover by metadata before posting; use a saved id for subsequent updates.

    History must be readable and pagination complete before creating a new message.
    At the page bound an error is raised instead of risking a duplicate. Scheduling,
    persistence and serialisation across workers belong to T-706.
    """

    def __init__(
        self,
        settings: SlackSettings,
        *,
        token: str | None = None,
        client: SlackClient | None = None,
        history_page_size: int = 100,
        max_history_pages: int = 100,
    ) -> None:
        if not 1 <= history_page_size <= 200 or max_history_pages < 1:
            raise SlackError("invalid_configuration", retryable=False)
        self._settings = settings
        self._client = client if client is not None else SlackClient(token)
        self._page_size = history_page_size
        self._max_pages = max_history_pages

    def _find(self, post: SlackPost) -> str | None:
        cursor = ""
        seen: set[str] = set()
        for _ in range(self._max_pages):
            page = self._client.history(post.channel, cursor=cursor, limit=self._page_size)
            messages = page.get("messages")
            if not isinstance(messages, list):
                raise SlackError("invalid_response", retryable=True)
            for message in messages:
                if not isinstance(message, dict):
                    raise SlackError("invalid_response", retryable=True)
                metadata = message.get("metadata")
                if metadata is None:
                    continue
                if not isinstance(metadata, dict):
                    raise SlackError("invalid_response", retryable=True)
                if metadata.get("event_type") != "reel_video":
                    continue
                payload = metadata.get("event_payload")
                if not isinstance(payload, dict):
                    raise SlackError("invalid_response", retryable=True)
                job_id = payload.get("job_id")
                if type(job_id) not in {str, int} or str(job_id) != str(post.job_id):
                    continue
                if payload.get("instance_id") == post.instance_id:
                    _external_id(post.channel, message.get("ts"))
                    return message["ts"]
            response_metadata = page.get("response_metadata", {})
            if (
                not isinstance(response_metadata, dict)
                or type(page.get("has_more", False)) is not bool
            ):
                raise SlackError("invalid_response", retryable=True)
            next_cursor = response_metadata.get("next_cursor", "")
            if not isinstance(next_cursor, str):
                raise SlackError("invalid_response", retryable=True)
            next_cursor = next_cursor.strip()
            if not next_cursor:
                if page.get("has_more", False):
                    raise SlackError("history_incomplete", retryable=True)
                return None
            if next_cursor in seen:
                raise SlackError("history_incomplete", retryable=True)
            seen.add(next_cursor)
            cursor = next_cursor
        raise SlackError("history_incomplete", retryable=True)

    def publish(self, post: SlackPost, previous: str | None = None) -> SlackResult:
        """Render offline or update/recover/post live, then fetch a permalink.

        A permalink failure carries external_id on SlackError so T-706 can save the
        already committed message id even though the publication is not yet complete.
        """
        text = render_message(self._settings.message_template, post)
        if self._settings.mode == "dry-run":
            log.info("Dry-run Slack publication prepared for job %s", post.job_id)
            return SlackResult(None, None, text, created=False, dry_run=True)
        _validate_channel(post.channel)
        if previous is not None:
            channel, separator, ts = previous.partition(":")
            if not separator or not _TS.fullmatch(ts):
                raise SlackError("invalid_external_id", retryable=False)
            if channel != post.channel:
                raise SlackError("previous_channel_mismatch", retryable=False)
        else:
            ts = self._find(post)
        created = ts is None
        metadata = post.metadata()
        if created:
            response = self._client.post_message(post.channel, text, metadata)
        else:
            response = self._client.update_message(post.channel, ts, text, metadata)
        external_id = _external_id(post.channel, response.get("ts"))
        if response.get("channel") != post.channel or (not created and response["ts"] != ts):
            raise SlackError(
                "invalid_response",
                retryable=True,
                external_id=None if created else f"{post.channel}:{ts}",
            )
        try:
            url = self._client.get_permalink(post.channel, response["ts"])
        except SlackError as error:
            error.external_id = external_id
            raise
        return SlackResult(external_id, url, text, created=created, dry_run=False)


def make_slack_publisher(cfg: LoadedConfig) -> SlackPublisher:
    """Use the configured mode/template and the existing secret loader."""
    return SlackPublisher(cfg.settings.slack, token=cfg.secrets.get("SLACK_BOT_TOKEN"))
