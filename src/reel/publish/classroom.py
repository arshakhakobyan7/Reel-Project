"""Published Classroom link cards with recovery and checkpointed replacement (T-704).

The caller serialises attempts for a job and persists every on_state snapshot.
Stable API link attachments are immutable for both supported published post types.
"""

import copy
import hashlib
import logging
import re
import string
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as Date
from typing import Any, Literal
from urllib.parse import quote, urlencode, urlsplit

from reel.config.schema import POST_FIELDS, ClassroomSettings, check_template
from reel.publish.google_api import AccessTokens, GoogleApiClient, GoogleApiError

log = logging.getLogger("reel.publish.classroom")
API_URL = "https://classroom.googleapis.com/v1/"
PostType = Literal["announcement", "material"]
_RESOURCES = {"announcement": "announcements", "material": "courseWorkMaterials"}
_LIST_KEYS = {"announcement": "announcements", "material": "courseWorkMaterial"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
_COURSE = re.compile(r"[A-Za-z0-9_:.-]{1,200}\Z")
StateHook = Callable[[dict[str, Any]], None]
Check = Callable[[], None]


def _error(message: str, reason: str, *, retryable: bool = False) -> GoogleApiError:
    return GoogleApiError(message, status=None, reason=reason, retryable=retryable)


def _invalid_response() -> GoogleApiError:
    return _error(
        "Classroom returned an invalid post or history response.",
        "invalid_response",
        retryable=True,
    )


@dataclass(frozen=True)
class ClassroomPost:
    job_id: int
    course_id: str
    title: str
    url: str
    summary: str = ""
    group: str = ""
    lesson: int | str = ""
    date: Date | str = ""
    duration: float | str = ""
    instance_id: str | None = None

    def marker(self) -> str:
        """A short reference footer, optionally namespaced by the database instance."""
        if type(self.job_id) is not int or self.job_id < 1:
            raise _error("Classroom job_id must be a positive integer.", "invalid_post")
        if self.instance_id is not None and (
            not isinstance(self.instance_id, str) or not _ID.fullmatch(self.instance_id)
        ):
            raise _error("Classroom instance_id is invalid.", "invalid_post")
        identity = f"reel:{self.instance_id or 'local'}:job:{self.job_id}"
        return "Reel · " + hashlib.sha256(identity.encode("ascii")).hexdigest()[:16]


@dataclass(frozen=True)
class ClassroomResult:
    post_id: str | None
    post_type: PostType
    url: str | None
    created: bool
    dry_run: bool
    body: dict[str, Any]
    details: dict[str, Any]

    @property
    def updated(self) -> bool:
        return not self.dry_run and self.post_id is not None and not self.created


def render_text(template: str, post: ClassroomPost) -> str:
    """Render simple POST_FIELDS and append a short, stable reference line."""
    formatter = string.Formatter()
    parts = []
    try:
        check_template(template, POST_FIELDS)
        for literal, name, spec, conversion in formatter.parse(template):
            parts.append(literal)
            if name is None:
                continue
            if name not in POST_FIELDS or "{" in spec or "}" in spec:
                raise ValueError("only simple fields are supported")
            value = getattr(post, name)
            if conversion:
                value = formatter.convert_field(value, conversion)
            parts.append(format(value, spec))
        text = "".join(parts).strip()
        if not text:
            raise ValueError("empty text")
        text = text + "\n\n" + post.marker()
        text.encode("utf-8")
        if len(text) > 30_000:
            raise ValueError("text too long")
        return text
    except ValueError, TypeError, KeyError, AttributeError, UnicodeError:
        raise _error(
            "Classroom text template is invalid, empty or exceeds 30000 characters.",
            "invalid_template",
        ) from None


def _links(item: dict[str, Any]) -> list[str]:
    materials = item.get("materials", [])
    if not isinstance(materials, list):
        raise _invalid_response()
    links = []
    for material in materials:
        if not isinstance(material, dict):
            raise _invalid_response()
        if "link" in material:
            link = material["link"]
            if not isinstance(link, dict) or not isinstance(link.get("url"), str):
                raise _invalid_response()
            links.append(link["url"])
        if "youtubeVideo" in material:
            video = material["youtubeVideo"]
            if not isinstance(video, dict):
                raise _invalid_response()
            if isinstance(video.get("alternateLink"), str):
                links.append(video["alternateLink"])
            if video.get("id"):
                links.append("https://www.youtube.com/watch?v=" + _identifier(video["id"]))
    return links


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise _invalid_response()
    return value


def _reference(post_id: str, post_type: str) -> dict[str, str]:
    if post_type not in _RESOURCES:
        raise _error("Stored Classroom post type is invalid.", "invalid_state")
    return {"post_id": _identifier(post_id), "post_type": post_type}


class ClassroomPublisher:
    """Publish link cards; OAuth, persistence and retry scheduling belong to the caller."""

    def __init__(
        self,
        settings: ClassroomSettings,
        tokens: AccessTokens | None = None,
        *,
        api_url: str = API_URL,
        timeout_seconds: float = 30,
        page_size: int = 100,
        max_history_pages: int = 100,
    ) -> None:
        if (
            type(page_size) is not int
            or not 1 <= page_size <= 100
            or type(max_history_pages) is not int
            or max_history_pages < 1
        ):
            raise _error("Invalid Classroom history page limits.", "invalid_configuration")
        if settings.mode == "live" and tokens is None:
            raise _error(
                "Google access tokens are required for live Classroom publishing.", "missing_tokens"
            )
        self.settings, self.tokens = settings, tokens
        self.api_url, self.timeout = api_url, timeout_seconds
        self.page_size, self.max_pages = page_size, max_history_pages

    def publish(
        self,
        post: ClassroomPost,
        previous: dict[str, Any] | None,
        *,
        on_state: StateHook | None = None,
        check: Check | None = None,
    ) -> ClassroomResult:
        return _Publication(self, post, previous, on_state, check).run()


class _Publication:
    def __init__(
        self,
        publisher: ClassroomPublisher,
        post: ClassroomPost,
        previous: dict[str, Any] | None,
        on_state: StateHook | None,
        check: Check | None,
    ) -> None:
        self.p, self.post = publisher, post
        self.check = check if check is not None else lambda: None
        self.on_state = on_state
        self.check()
        self.marker = post.marker()
        if (
            not isinstance(post.course_id, str)
            or (post.course_id and not _COURSE.fullmatch(post.course_id))
            or post.course_id in {".", ".."}
            or (not post.course_id and publisher.settings.mode == "live")
        ):
            raise _error(
                "A valid Classroom course id is required; "
                "the account must be a teacher of that course.",
                "invalid_course",
            )
        try:
            url = urlsplit(post.url)
            post.url.encode("utf-8")
            if len(post.url) > 2024:
                raise ValueError("link too long")
            if (
                url.scheme not in {"https", "http"}
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or any(ord(c) <= 32 or ord(c) == 127 for c in post.url)
            ):
                raise ValueError("invalid link")
        except ValueError, TypeError:
            raise _error(
                "Classroom video URL must be an HTTP or HTTPS link.", "invalid_post"
            ) from None
        text = render_text(publisher.settings.text_template, post)
        self.kind: PostType = publisher.settings.post_type
        self.body: dict[str, Any] = {
            "state": "PUBLISHED",
            "materials": [{"link": {"url": post.url}}],
        }
        if self.kind == "announcement":
            self.body["text"] = text
        else:
            if not isinstance(post.title, str) or not post.title.strip() or len(post.title) > 3000:
                raise _error(
                    "Classroom material title must contain 1 to 3000 characters.", "invalid_post"
                )
            try:
                post.title.encode("utf-8")
            except UnicodeError:
                raise _error(
                    "Classroom material title must be valid Unicode.", "invalid_post"
                ) from None
            self.body.update({"title": post.title, "description": text})
        old = previous or {}
        if old.get("dry_run"):
            old = {}
        if (
            old.get("job_id", post.job_id) != post.job_id
            or old.get("marker", self.marker) != self.marker
            or old.get("course_id", post.course_id) != post.course_id
        ):
            raise _error(
                "Stored Classroom state belongs to another job, instance or course.",
                "state_mismatch",
            )
        self.old = copy.deepcopy(old)
        self.pending = [
            _reference(item["post_id"], item["post_type"]) for item in old.get("pending_delete", [])
        ]
        self.state: dict[str, Any] = {
            "version": 1,
            "job_id": post.job_id,
            "marker": self.marker,
            "course_id": post.course_id,
            "post_type": self.kind,
            "video_url": post.url,
            "pending_delete": self.pending,
            "complete": False,
            "dry_run": publisher.settings.mode == "dry-run",
        }
        self.created = False
        self.client: GoogleApiClient

    def save(self) -> None:
        self.check()
        if self.on_state is not None:
            self.on_state(copy.deepcopy(self.state))

    def path(self, kind: str, post_id: str | None = None) -> str:
        path = f"courses/{quote(self.post.course_id, safe='')}/{_RESOURCES[kind]}"
        return path + "/" + quote(post_id, safe="") if post_id else path

    def call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.request(method, path, check=self.check, **kwargs)
            return response.json() if response.data else {}
        except GoogleApiError as error:
            if error.status == 403 and not error.retryable:
                raise GoogleApiError(
                    "Classroom access denied: the Google account must be a teacher of that course, "
                    "with the required OAuth scopes and the creating developer project.",
                    status=403,
                    reason="classroom_permission_denied",
                    retryable=False,
                ) from None
            raise

    def course_error(self, error: GoogleApiError) -> GoogleApiError:
        if error.status == 404:
            return GoogleApiError(
                "Classroom course not found or inaccessible: "
                "the Google account must be a teacher of that course.",
                status=404,
                reason="classroom_course_not_found",
                retryable=False,
            )
        return error

    def get(self, ref: dict[str, str]) -> dict[str, Any] | None:
        try:
            item = self.call("GET", self.path(ref["post_type"], ref["post_id"]))
        except GoogleApiError as error:
            if error.status == 404:
                return None
            raise
        if _identifier(item.get("id")) != ref["post_id"]:
            raise _invalid_response()
        return None if item.get("state") == "DELETED" else item

    def queue(self, ref: dict[str, str]) -> None:
        if ref not in self.pending:
            self.pending.append(ref)

    def find(self) -> dict[str, Any] | None:
        key = "text" if self.kind == "announcement" else "description"
        state_key = (
            "announcementStates" if self.kind == "announcement" else "courseWorkMaterialStates"
        )
        path = (
            self.path(self.kind) + "?" + urlencode([(state_key, "PUBLISHED"), (state_key, "DRAFT")])
        )
        token, seen, matches = "", set(), []
        for _ in range(self.p.max_pages):
            params: dict[str, Any] = {"pageSize": self.p.page_size, "orderBy": "updateTime desc"}
            if token:
                params["pageToken"] = token
            try:
                response = self.call("GET", path, params=params)
            except GoogleApiError as error:
                raise self.course_error(error) from None
            items = response.get(_LIST_KEYS[self.kind], [])
            if not isinstance(items, list):
                raise _invalid_response()
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get(key, ""), str):
                    raise _invalid_response()
                if item.get("state") == "DELETED":
                    continue
                own = item.get(key, "").splitlines()[-1:] == [self.marker]
                exact = self.post.url in _links(item)
                if own or exact:
                    ref = _reference(item.get("id"), self.kind)
                    matches.append((item, own, exact))
                    if own:
                        self.queue(ref)
            token = response.get("nextPageToken", "")
            if not token:
                break
            if not isinstance(token, str) or token in seen:
                raise _invalid_response()
            seen.add(token)
        else:
            raise _error(
                "Classroom history lookup exceeded its page limit; no post was created.",
                "lookup_incomplete",
                retryable=True,
            )
        # Prefer the desired link, including a replacement created before a crash.
        selected = next((item for item, own, exact in matches if own and exact), None)
        if selected is None:
            selected = next((item for item, own, exact in matches if exact), None)
        if selected is None:
            selected = next((item for item, own, exact in matches if own), None)
        return selected

    def patch(self, item: dict[str, Any]) -> dict[str, Any] | None:
        ref = _reference(item.get("id"), self.kind)
        body = {key: value for key, value in self.body.items() if key != "materials"}
        try:
            result = self.call(
                "PATCH",
                self.path(self.kind, ref["post_id"]),
                params={"updateMask": ",".join(body)},
                json_body=body,
            )
        except GoogleApiError as error:
            if error.status == 404 or (error.status == 400 and self.get(ref) is None):
                return None
            raise
        if _identifier(result.get("id")) != ref["post_id"]:
            raise _invalid_response()
        return result

    def remember(self, item: dict[str, Any]) -> None:
        self.state.update({"post_id": _identifier(item.get("id")), "post_type": self.kind})
        self.pending[:] = [
            r for r in self.pending if r != _reference(self.state["post_id"], self.kind)
        ]
        self.save()  # The id is durable before resolving the URL or deleting another post.

    def finish(self, item: dict[str, Any]) -> ClassroomResult:
        self.remember(item)
        link = item.get("alternateLink")
        if not link or item.get("state") != "PUBLISHED":
            item = self.get(_reference(self.state["post_id"], self.kind))
            if item is None:
                raise _invalid_response()
            link = item.get("alternateLink")
        try:
            parsed = urlsplit(link)
            if (
                item.get("state") != "PUBLISHED"
                or parsed.scheme != "https"
                or parsed.hostname != "classroom.google.com"
                or parsed.username is not None
                or parsed.password is not None
                or not parsed.path
                or any(ord(c) <= 32 or ord(c) == 127 for c in link)
            ):
                raise ValueError("invalid alternate link")
        except ValueError, TypeError:
            raise _invalid_response() from None
        self.state["url"] = link
        self.save()
        for ref in list(self.pending):
            try:
                self.call("DELETE", self.path(ref["post_type"], ref["post_id"]))
            except GoogleApiError as error:
                if error.status != 404 and not (error.status == 400 and self.get(ref) is None):
                    raise
            self.pending.remove(ref)
            self.save()
        self.state["complete"] = True
        self.save()
        log.info("Classroom publishing completed", extra={"job_id": self.post.job_id})
        return ClassroomResult(
            self.state["post_id"],
            self.kind,
            link,
            self.created,
            False,
            copy.deepcopy(self.body),
            copy.deepcopy(self.state),
        )

    def run(self) -> ClassroomResult:
        if self.p.settings.mode == "dry-run":
            self.state["body"] = copy.deepcopy(self.body)
            self.state["previous_post_id"] = self.old.get("post_id")
            self.save()
            return ClassroomResult(
                None,
                self.kind,
                None,
                False,
                True,
                copy.deepcopy(self.body),
                copy.deepcopy(self.state),
            )
        assert self.p.tokens is not None
        self.client = GoogleApiClient(
            self.p.tokens, base_url=self.p.api_url, timeout_seconds=self.p.timeout
        )
        if self.old.get("post_id"):
            ref = _reference(self.old["post_id"], self.old["post_type"])
            item = self.get(ref)
            if item is not None:
                if ref["post_type"] == self.kind and self.post.url in _links(item):
                    patched = self.patch(item)
                    if patched is not None:
                        return self.finish(patched)
                else:
                    self.queue(ref)
        for _ in range(3):
            item = self.find()
            self.save()  # Replacement intent survives a lost create response.
            if item is not None and self.post.url in _links(item):
                patched = self.patch(item)
                if patched is None:
                    continue
                return self.finish(patched)
            try:
                item = self.call("POST", self.path(self.kind), json_body=self.body)
            except GoogleApiError as error:
                raise self.course_error(error) from None
            self.created = True
            return self.finish(item)
        raise _error(
            "Classroom posts kept disappearing during publishing.", "post_changed", retryable=True
        )
