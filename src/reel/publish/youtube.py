"""Unlisted YouTube publishing with checkpointed resumable uploads (T-702).

The caller serialises attempts for a job and durably saves every on_state snapshot.
There is no server-side idempotency key: recovery searches bounded recent uploads.
"""

import copy
import logging
import mimetypes
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from os import stat_result
from pathlib import Path
from typing import Any

from reel.config.schema import YoutubeSettings
from reel.logging_setup import register_secret
from reel.publish.google_api import (
    AccessTokens,
    GoogleApiClient,
    GoogleApiError,
    GoogleResponse,
    SessionCodec,
    default_session_codec,
)

log = logging.getLogger("reel.publish.youtube")
PRIVACY = "unlisted"
API_URL = "https://www.googleapis.com/youtube/v3/"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/"
CHUNK_UNIT = 256 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SHA = re.compile(r"[0-9a-fA-F]{64}\Z")
_RANGE = re.compile(r"bytes=0-(\d+)\Z")
_QUOTA = {"quotaExceeded", "uploadLimitExceeded"}
_REJECTION = {
    "aborted",
    "codec",
    "conversion",
    "emptyFile",
    "invalidFile",
    "tooSmall",
    "uploadAborted",
    "duplicate",
    "inappropriate",
    "copyright",
    "termsOfUse",
    "uploaderAccountSuspended",
    "length",
    "claim",
    "uploaderAccountClosed",
    "failedProcessing",
}

StateHook = Callable[[dict[str, Any]], None]
ProgressHook = Callable[[int, int], None]
Check = Callable[[], None]


@dataclass(frozen=True)
class YoutubeUpload:
    job_id: int
    marker: str
    video: Path | str
    file_sha256: str
    title: str
    description: str
    thumbnail: Path | str | None = None
    playlist_id: str = ""
    group_name: str = ""


@dataclass(frozen=True)
class YoutubeResult:
    video_id: str
    url: str
    playlist_id: str
    created: bool
    updated: bool
    replaced_video_ids: tuple[str, ...]
    flags: tuple[str, ...]
    dry_run: bool
    details: dict[str, Any]


def _fail(message: str, reason: str, *, retryable: bool = False) -> GoogleApiError:
    return GoogleApiError(message, status=None, reason=reason, retryable=retryable)


def _id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise _fail(
            "YouTube returned an invalid resource identifier.", "invalid_response", retryable=True
        )
    return value


def _items(response: dict[str, Any]) -> list[dict[str, Any]]:
    items = response.get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise _fail(
            "YouTube returned an invalid resource list.", "invalid_response", retryable=True
        )
    return items


def _clean(text: str) -> str:
    return text.replace("<", "(").replace(">", ")")


def _metadata(upload: YoutubeUpload, settings: YoutubeSettings) -> dict[str, Any]:
    title = _clean(upload.title).strip()[:100]
    if not title:
        raise _fail("YouTube title must not be empty.", "invalid_upload")
    try:
        description = (
            _clean(upload.description).encode("utf-8")[:5000].decode("utf-8", errors="ignore")
        )
        title.encode("utf-8")
    except UnicodeError:
        raise _fail("YouTube metadata must be valid Unicode.", "invalid_upload") from None
    return {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": settings.category_id,
            "defaultLanguage": settings.default_language,
            "defaultAudioLanguage": settings.default_language,
            "tags": [upload.marker, "reel-file-" + upload.file_sha256[:16].lower()],
        },
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
    }


def _stat(path: Path) -> stat_result:
    try:
        info = path.stat()
        if not path.is_file() or info.st_size == 0:
            raise _fail("YouTube media must be a nonempty regular file.", "invalid_upload")
        return info
    except OSError:
        raise _fail(
            "Cannot read a YouTube media file.", "media_unavailable", retryable=True
        ) from None


class _Expired(Exception):
    """The upload session expired; a marker lookup must precede another session."""


class YoutubePublisher:
    """Publish without database access; OAuth and stage retry scheduling stay with the caller."""

    def __init__(
        self,
        settings: YoutubeSettings,
        tokens: AccessTokens | None = None,
        *,
        api_url: str = API_URL,
        upload_url: str = UPLOAD_URL,
        chunk_size: int = 8 * 1024 * 1024,
        sleep: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 30,
        max_retries: int = 5,
        max_pages: int = 10,
        session_codec: SessionCodec | None = None,
    ) -> None:
        if type(chunk_size) is not int or chunk_size < CHUNK_UNIT or chunk_size % CHUNK_UNIT:
            raise _fail(
                "YouTube chunk_size must be a positive multiple of 256 KiB.",
                "invalid_configuration",
            )
        if (
            type(max_retries) is not int
            or max_retries < 0
            or type(max_pages) is not int
            or max_pages < 1
        ):
            raise _fail("Invalid YouTube retry or page limit.", "invalid_configuration")
        if settings.mode == "live" and tokens is None:
            raise _fail(
                "Google access tokens are required for live YouTube publishing.", "missing_tokens"
            )
        self.settings = settings
        self.tokens = tokens
        self.api_url, self.upload_url = api_url, upload_url
        self.chunk_size, self.sleep = chunk_size, sleep
        self.timeout, self.max_retries, self.max_pages = timeout_seconds, max_retries, max_pages
        self.codec = session_codec if session_codec is not None else default_session_codec(tokens)

    def publish(
        self,
        upload: YoutubeUpload,
        previous: dict[str, Any] | None,
        *,
        on_state: StateHook,
        on_progress: ProgressHook | None = None,
        check: Check | None = None,
    ) -> YoutubeResult:
        return _Publication(self, upload, previous, on_state, on_progress, check).run()


class _Publication:
    def __init__(
        self,
        publisher: YoutubePublisher,
        upload: YoutubeUpload,
        previous: dict[str, Any] | None,
        on_state: StateHook,
        on_progress: ProgressHook | None,
        check: Check | None,
    ) -> None:
        self.p = publisher
        self.u = upload
        self.on_state, self.on_progress = on_state, on_progress
        self.check = check if check is not None else lambda: None
        self.check()
        if (
            type(upload.job_id) is not int
            or upload.job_id < 1
            or not isinstance(upload.marker, str)
            or not _ID.fullmatch(upload.marker)
            or not isinstance(upload.file_sha256, str)
            or not _SHA.fullmatch(upload.file_sha256)
            or (upload.playlist_id and not _ID.fullmatch(upload.playlist_id))
        ):
            raise _fail(
                "Invalid YouTube job identity, marker, file hash or playlist id.", "invalid_upload"
            )
        self.video = Path(upload.video)
        self.info = _stat(self.video)
        self.total = self.info.st_size
        self.body = _metadata(upload, publisher.settings)
        self.old = copy.deepcopy(previous or {})
        if self.old.get("dry_run"):
            self.old = {}
        if self.old and (
            self.old.get("marker") != upload.marker or self.old.get("job_id") != upload.job_id
        ):
            raise _fail(
                "Stored YouTube state belongs to a different job or marker.", "state_mismatch"
            )
        self.state = copy.deepcopy(self.old)
        self.flags: list[str] = []
        self.created = False
        self.api: GoogleApiClient
        self.media: GoogleApiClient

    def save(self) -> None:
        self.check()
        self.on_state(copy.deepcopy(self.state))

    def call(
        self, client: GoogleApiClient, method: str, path: str, **kwargs: Any
    ) -> GoogleResponse:
        return client.request(method, path, check=self.check, **kwargs)

    def pages(
        self, path: str, params: dict[str, Any], *, bounded_recent: bool = False
    ) -> Iterator[list[dict[str, Any]]]:
        token = ""
        seen = set()
        for _ in range(self.p.max_pages):
            query = {**params, "maxResults": 50}
            if token:
                query["pageToken"] = token
            response = self.call(self.api, "GET", path, params=query).json()
            yield _items(response)
            token = response.get("nextPageToken", "")
            if not token:
                return
            if not isinstance(token, str) or token in seen:
                raise _fail(
                    "YouTube pagination did not advance.", "invalid_response", retryable=True
                )
            seen.add(token)
        if not bounded_recent:
            raise _fail(
                "YouTube lookup exceeded its page limit.", "lookup_incomplete", retryable=True
            )

    def run(self) -> YoutubeResult:
        if self.p.settings.mode == "dry-run":
            return self.dry_run()
        assert self.p.tokens is not None
        # Verify state protection before creating any external upload session.
        if (
            self.p.codec.unseal(self.p.codec.seal("reel-session-protection-check"))
            != "reel-session-protection-check"
        ):
            raise _fail(
                "The session codec did not restore protected state.", "invalid_configuration"
            )
        self.api = GoogleApiClient(
            self.p.tokens,
            base_url=self.p.api_url,
            allowed_urls=(self.p.upload_url,),
            timeout_seconds=self.p.timeout,
        )
        self.media = GoogleApiClient(
            self.p.tokens,
            base_url=self.p.upload_url,
            allowed_urls=(self.p.api_url,),
            timeout_seconds=self.p.timeout,
        )
        same = self.old.get("file_sha256") == self.u.file_sha256.lower()
        if self.old and not same:
            old_id = self.old.get("video_id")
            if old_id:
                retired = self.state.setdefault("retirements", [])
                if not any(item["video_id"] == old_id for item in retired):
                    retired.append(
                        {
                            "video_id": _id(old_id),
                            "playlist_id": self.old.get("playlist_id", ""),
                            "removed": False,
                            "deleted": False,
                            "delete": self.p.settings.delete_replaced_videos,
                        }
                    )
            for key in ("video_id", "session", "upload_offset", "complete"):
                self.state.pop(key, None)
        self.state.update(
            {
                "version": 1,
                "job_id": self.u.job_id,
                "marker": self.u.marker,
                "file_sha256": self.u.file_sha256.lower(),
                "file_size": self.total,
                "group_name": self.u.group_name,
                "dry_run": False,
                "complete": False,
                "playlist_done": False,
            }
        )
        self.save()
        if self.state.get("video_id"):
            video_id = _id(self.state["video_id"])
            self.update(video_id)
        else:
            video_id = self.obtain_video()
        self.check_status(video_id)
        self.thumbnail(video_id)
        playlist = self.playlist()
        if playlist:
            self.add_to_playlist(playlist, video_id)
        self.state.update({"playlist_id": playlist, "playlist_done": True})
        self.save()
        self.retire(video_id)
        self.state.update({"flags": self.flags, "complete": True})
        self.save()
        log.info("YouTube publishing completed", extra={"job_id": self.u.job_id})
        return self.result(video_id, playlist, dry_run=False)

    def result(self, video_id: str, playlist: str, *, dry_run: bool) -> YoutubeResult:
        replaced = tuple(item["video_id"] for item in self.state.get("retirements", []))
        return YoutubeResult(
            video_id,
            f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
            playlist,
            self.created,
            not self.created,
            replaced,
            tuple(self.flags),
            dry_run,
            copy.deepcopy(self.state),
        )

    def dry_run(self) -> YoutubeResult:
        existing = self.old.get("video_id", "")
        same = bool(existing and self.old.get("file_sha256") == self.u.file_sha256.lower())
        self.created = not same
        playlist = self.u.playlist_id or self.old.get("playlist_id", "")
        action = (
            "use"
            if playlist
            else ("find_or_create" if self.p.settings.create_missing_playlists else "none")
        )
        playlist_body = self.playlist_body() if action == "find_or_create" else None
        self.state = {
            "version": 1,
            "job_id": self.u.job_id,
            "marker": self.u.marker,
            "dry_run": True,
            "file_sha256": self.u.file_sha256.lower(),
            "file_size": self.total,
            "metadata": self.body,
            "video_action": {
                "action": "update" if same else "upload",
                "method": "PUT" if same else "POST",
                "part": "snippet" if same else "snippet,status",
                "body": {"id": existing, "snippet": self.body["snippet"]} if same else self.body,
            },
            "playlist_id": playlist,
            "playlist_action": {"action": action, "id": playlist, "body": playlist_body},
            "thumbnail_size": _stat(Path(self.u.thumbnail)).st_size if self.u.thumbnail else None,
            "retirements": [
                {"video_id": existing, "delete": self.p.settings.delete_replaced_videos}
            ]
            if existing and not same
            else [],
        }
        if not playlist and action == "none":
            self.flags.append("youtube_no_playlist")
        self.state["flags"] = self.flags
        self.save()
        return self.result(_id(existing) if same else "", playlist, dry_run=True)

    def update(self, video_id: str) -> None:
        self.call(
            self.api,
            "PUT",
            "videos",
            params={"part": "snippet"},
            json_body={"id": video_id, "snippet": self.body["snippet"]},
        )

    def recent_match(self) -> str | None:
        channels = _items(
            self.call(
                self.api, "GET", "channels", params={"part": "contentDetails", "mine": "true"}
            ).json()
        )
        if not channels:
            raise _fail("The Google account has no YouTube channel.", "channel_not_found")
        try:
            uploads = _id(channels[0]["contentDetails"]["relatedPlaylists"]["uploads"])
        except KeyError, TypeError:
            raise _fail(
                "YouTube did not return the channel uploads playlist.",
                "invalid_response",
                retryable=True,
            ) from None
        tags = set(self.body["snippet"]["tags"])
        for items in self.pages(
            "playlistItems", {"part": "contentDetails", "playlistId": uploads}, bounded_recent=True
        ):
            try:
                ids = [_id(item["contentDetails"]["videoId"]) for item in items]
            except KeyError, TypeError:
                raise _fail(
                    "YouTube returned invalid upload history.", "invalid_response", retryable=True
                ) from None
            if not ids:
                continue
            videos = _items(
                self.call(
                    self.api, "GET", "videos", params={"part": "snippet", "id": ",".join(ids)}
                ).json()
            )
            for item in videos:
                snippet = item.get("snippet")
                if (
                    not isinstance(snippet, dict)
                    or not isinstance(snippet.get("tags", []), list)
                    or any(not isinstance(tag, str) for tag in snippet.get("tags", []))
                ):
                    raise _fail(
                        "YouTube returned invalid video metadata.",
                        "invalid_response",
                        retryable=True,
                    )
                if tags.issubset(snippet.get("tags", [])):
                    return _id(item.get("id"))
        return None

    def remember_video(self, response: GoogleResponse) -> str:
        video_id = _id(response.json().get("id"))
        self.state["video_id"] = video_id
        self.state["upload_offset"] = self.total
        self.state.pop("session", None)
        self.save()  # Persist the id before status, thumbnail or playlist can fail.
        if self.on_progress is not None:
            self.on_progress(self.total, self.total)
        return video_id

    def obtain_video(self) -> str:
        for _ in range(self.p.max_retries + 1):
            protected = self.state.get("session")
            if protected:
                session = self.p.codec.unseal(protected)
                register_secret(session)
                self.created = True
                try:
                    return self.transfer(session, query=True)
                except _Expired:
                    self.state.pop("session", None)
                    self.state.pop("upload_offset", None)
                    self.save()
            found = self.recent_match()
            if found:
                self.state["video_id"] = found
                self.state.pop("session", None)
                self.save()
                self.update(found)
                return found
            response = self.call(
                self.media,
                "POST",
                "videos",
                params={"uploadType": "resumable", "part": "snippet,status"},
                json_body=self.body,
                headers={
                    "X-Upload-Content-Length": str(self.total),
                    "X-Upload-Content-Type": self.mime(),
                },
            )
            session = response.headers.get("location", "")
            if not session:
                raise _fail(
                    "YouTube did not return an upload session.", "invalid_response", retryable=True
                )
            register_secret(session)
            self.state.update({"session": self.p.codec.seal(session), "upload_offset": 0})
            self.save()  # Must happen before sending any media bytes.
            self.created = True
            try:
                return self.transfer(session, query=False)
            except _Expired:
                self.state.pop("session", None)
                self.state.pop("upload_offset", None)
                self.save()
        raise _fail("YouTube upload sessions keep expiring.", "session_expired", retryable=True)

    def mime(self) -> str:
        mime = mimetypes.guess_type(self.video.name)[0]
        return mime if mime and mime.startswith("video/") else "application/octet-stream"

    def pause(self, failures: int, response: GoogleResponse | None = None) -> None:
        if failures > self.p.max_retries:
            raise _fail(
                "YouTube upload retry limit reached; saved state can resume.",
                "upload_interrupted",
                retryable=True,
            )
        delay = min(2 ** (failures - 1), 32)
        if response is not None:
            try:
                retry_after = float(response.headers.get("retry-after", "0"))
                if 0 <= retry_after <= 60:
                    delay = max(delay, retry_after)
            except ValueError:
                return self.wait(delay)
        self.wait(delay)

    def wait(self, delay: float) -> None:
        self.check()
        self.p.sleep(delay)
        self.check()

    def confirmed(self, response: GoogleResponse) -> int:
        value = response.headers.get("range")
        if value is None:
            return 0
        match = _RANGE.fullmatch(value.strip())
        if match is None or not 0 < int(match[1]) + 1 <= self.total:
            raise _fail(
                "YouTube returned an invalid upload offset.", "invalid_response", retryable=True
            )
        return int(match[1]) + 1

    def transfer(self, session: str, *, query: bool) -> str:
        offset, failures = 0, 0
        try:
            stream = self.video.open("rb")
        except OSError:
            raise _fail(
                "Cannot open the YouTube media file.", "media_unavailable", retryable=True
            ) from None
        with stream:
            while True:
                self.check()
                current = _stat(self.video)
                if (current.st_size, current.st_mtime_ns) != (self.total, self.info.st_mtime_ns):
                    raise _fail("The YouTube media file changed during upload.", "media_changed")
                if query:
                    data, headers = b"", {"Content-Range": f"bytes */{self.total}"}
                else:
                    stream.seek(offset)
                    data = stream.read(min(self.p.chunk_size, self.total - offset))
                    if not data:
                        query = True
                        failures += 1
                        self.pause(failures)
                        continue
                    headers = {
                        "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{self.total}",
                        "Content-Type": self.mime(),
                    }
                try:
                    response = self.call(
                        self.media,
                        "PUT",
                        session,
                        data=data,
                        headers=headers,
                        accepted=(200, 201, 308),
                    )
                except GoogleApiError as error:
                    if error.status in {404, 410}:
                        raise _Expired from None
                    if not error.retryable or error.reason in _QUOTA:
                        raise
                    failures += 1
                    self.pause(failures)
                    query = True  # Never guess how much of a failed chunk reached Google.
                    continue
                if response.status in {200, 201}:
                    return self.remember_video(response)
                confirmed = self.confirmed(response)
                if confirmed < offset:
                    raise _fail(
                        "YouTube upload offset moved backwards.", "invalid_response", retryable=True
                    )
                if confirmed == offset and not query:
                    failures += 1
                    self.pause(failures, response)
                    query = True
                    continue
                if confirmed > offset:
                    failures = 0
                offset = confirmed
                self.state["upload_offset"] = offset
                self.save()
                if self.on_progress is not None:
                    self.on_progress(offset, self.total)
                if offset == self.total:
                    failures += 1
                    self.pause(failures, response)
                    query = True
                else:
                    query = False

    def check_status(self, video_id: str) -> None:
        items = _items(
            self.call(self.api, "GET", "videos", params={"part": "status", "id": video_id}).json()
        )
        if not items:
            raise _fail(
                "YouTube has not returned the uploaded video's status yet.",
                "status_pending",
                retryable=True,
            )
        status = items[0].get("status")
        if not isinstance(status, dict) or not isinstance(status.get("uploadStatus"), str):
            raise _fail(
                "YouTube returned invalid upload status.", "invalid_response", retryable=True
            )
        upload_status = status.get("uploadStatus")
        if upload_status in {"rejected", "failed"}:
            reason = status.get("rejectionReason") or status.get("failureReason")
            reason = reason if isinstance(reason, str) and reason in _REJECTION else "unspecified"
            raise _fail(
                f"YouTube upload was {upload_status} ({reason}).", f"upload_{upload_status}"
            )
        if status.get("privacyStatus") != PRIVACY:
            self.flags.append("youtube_not_unlisted")

    def thumbnail(self, video_id: str) -> None:
        if not self.u.thumbnail:
            return
        path = Path(self.u.thumbnail)
        info = _stat(path)
        if info.st_size >= 2_000_000:
            raise _fail("YouTube thumbnail must be under 2 MB.", "invalid_thumbnail")
        try:
            data = path.read_bytes()
        except OSError:
            raise _fail(
                "Cannot read the YouTube thumbnail.", "media_unavailable", retryable=True
            ) from None
        try:
            self.call(
                self.media,
                "POST",
                "thumbnails/set",
                params={"videoId": video_id, "uploadType": "media"},
                data=data,
                headers={
                    "Content-Type": "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
                },
            )
        except GoogleApiError as error:
            if error.status == 403 and not error.retryable:
                self.flags.append("youtube_thumbnail_not_set")
            else:
                raise

    def playlist_body(self) -> dict[str, Any]:
        title = _clean(self.u.group_name).strip()[:150]
        if not title:
            raise _fail("A group name is required to create a YouTube playlist.", "invalid_upload")
        return {
            "snippet": {"title": title, "defaultLanguage": self.p.settings.default_language},
            "status": {"privacyStatus": PRIVACY},
        }

    def playlist(self) -> str:
        playlist = self.u.playlist_id or self.state.get("playlist_id", "")
        if playlist:
            return _id(playlist)
        if not self.p.settings.create_missing_playlists:
            self.flags.append("youtube_no_playlist")
            return ""
        body = self.playlist_body()
        for items in self.pages("playlists", {"part": "snippet", "mine": "true"}):
            for item in items:
                if item.get("snippet", {}).get("title") == body["snippet"]["title"]:
                    playlist = _id(item.get("id"))
                    break
            if playlist:
                break
        if not playlist:
            response = self.call(
                self.api, "POST", "playlists", params={"part": "snippet,status"}, json_body=body
            )
            playlist = _id(response.json().get("id"))
        self.state["playlist_id"] = playlist
        self.save()
        return playlist

    def playlist_items(self, playlist: str, video_id: str) -> list[str]:
        return [
            _id(item.get("id"))
            for page in self.pages(
                "playlistItems", {"part": "id", "playlistId": playlist, "videoId": video_id}
            )
            for item in page
        ]

    def add_to_playlist(self, playlist: str, video_id: str) -> None:
        if not self.playlist_items(playlist, video_id):
            self.call(
                self.api,
                "POST",
                "playlistItems",
                params={"part": "snippet"},
                json_body={
                    "snippet": {
                        "playlistId": playlist,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            )

    def retire(self, current: str) -> None:
        for item in self.state.get("retirements", []):
            old = _id(item["video_id"])
            if old == current:
                continue
            if not item.get("removed"):
                if item.get("playlist_id"):
                    for entry in self.playlist_items(_id(item["playlist_id"]), old):
                        self.delete("playlistItems", entry)
                item["removed"] = True
                self.save()
            if (
                self.p.settings.delete_replaced_videos
                and item.get("delete")
                and not item.get("deleted")
            ):
                self.delete("videos", old)
                item["deleted"] = True
                self.save()
        self.state["retained_video_ids"] = [
            item["video_id"]
            for item in self.state.get("retirements", [])
            if not item.get("deleted") and item["video_id"] != current
        ]

    def delete(self, resource: str, identifier: str) -> None:
        try:
            self.call(self.api, "DELETE", resource, params={"id": identifier})
        except GoogleApiError as error:
            if error.status not in {404, 410}:
                raise
