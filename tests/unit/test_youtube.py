"""YouTube publishing and Google transport against a local fake service (T-702)."""

import copy
import hashlib
import json
import logging
import os
from dataclasses import replace
from urllib.error import URLError

import pytest
from youtube_fake import YoutubeFake

from reel.config.schema import YoutubeSettings
from reel.publish.google_api import GoogleApiClient, GoogleApiError, WindowsSessionCodec
from reel.publish.youtube import CHUNK_UNIT, PRIVACY, YoutubePublisher, YoutubeUpload

TOKEN = "fake-access-token-for-youtube-tests"
NEW_TOKEN = "refreshed-access-token-for-youtube-tests"


class Tokens:
    def __init__(self):
        self.invalidations = self.calls = 0

    def token(self):
        self.calls += 1
        return NEW_TOKEN if self.invalidations else TOKEN

    def invalidate(self):
        self.invalidations += 1


class TestCodec:
    """An injected in-memory test vault; production uses the separately tested DPAPI codec."""

    __test__ = False

    def __init__(self):
        self.values = {}

    def seal(self, url):
        key = "test-vault-" + str(len(self.values))
        self.values[key] = url
        return key

    def unseal(self, protected):
        return self.values[protected]


class Crash(Exception):
    """Simulated process termination at a persisted checkpoint."""


@pytest.fixture
def fake():
    with YoutubeFake() as service:
        yield service


@pytest.fixture
def upload(tmp_path):
    video = tmp_path / "lesson.mp4"
    video.write_bytes(bytes(range(256)) * (2 * CHUNK_UNIT // 256) + b"last Armenian lesson")
    thumb = tmp_path / "thumbnail.jpg"
    thumb.write_bytes(b"\xff\xd8synthetic-thumbnail\xff\xd9")
    return YoutubeUpload(
        42,
        "reel-12345678-job-42",
        video,
        hashlib.sha256(video.read_bytes()).hexdigest(),
        "Սեփական ֆունկցիաներ և hash աղյուսակներ",
        "Հայերեն նկարագրություն\n0:00 Ներածություն",
        thumbnail=thumb,
        playlist_id="PL_GROUP",
        group_name="Python 12 · Դասեր",
    )


def publisher(fake, *, tokens=None, codec=None, **options):
    settings = YoutubeSettings(mode="live", **options.pop("settings", {}))
    waits = options.pop("sleep", lambda seconds: None)
    return YoutubePublisher(
        settings,
        tokens if tokens is not None else Tokens(),
        api_url=fake.url + "/api/",
        upload_url=fake.url + "/upload/",
        chunk_size=CHUNK_UNIT,
        sleep=waits,
        session_codec=codec if codec is not None else TestCodec(),
        **options,
    )


def post_bodies(fake, path):
    return [c["body"] for c in fake.calls if c["method"] == "POST" and c["path"] == path]


def test_upload_bodies_ranges_checkpoints_progress_and_unlisted_privacy(fake, upload):
    states, progress, observations = [], [], []

    def state(details):
        states.append(details)
        observations.append((copy.deepcopy(details), len(fake.videos), len(fake.entries)))

    result = publisher(fake, settings={"create_missing_playlists": True}).publish(
        replace(upload, playlist_id=""),
        None,
        on_state=state,
        on_progress=lambda sent, total: progress.append((sent, total)),
    )
    assert result.created and not result.updated and not result.dry_run
    assert result.url == f"https://www.youtube.com/watch?v={result.video_id}"
    assert result.details == states[-1] and result.details["complete"]
    assert result.playlist_id and len(fake.entries) == 1
    for call in fake.calls:
        body = call.get("body")
        if body and "status" in body:
            assert body["status"]["privacyStatus"] == PRIVACY == "unlisted"
    body = post_bodies(fake, "/upload/videos")[0]
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert body["snippet"] == {
        "title": upload.title,
        "description": upload.description,
        "categoryId": "27",
        "defaultLanguage": "hy",
        "defaultAudioLanguage": "hy",
        "tags": [upload.marker, "reel-file-" + upload.file_sha256[:16]],
    }
    session = next(iter(fake.sessions.values()))
    assert bytes(session["data"]) == upload.video.read_bytes()
    assert next(row for row in observations if row[0].get("session"))[1:] == (0, 0)
    assert next(row for row in observations if row[0].get("video_id"))[2] == 0
    chunks = [
        c for c in fake.calls if c.get("range", "").startswith("bytes ") and "*/" not in c["range"]
    ]
    assert [c["received"] for c in chunks[:-1]] == [CHUNK_UNIT, CHUNK_UNIT]
    assert progress[-1] == (upload.video.stat().st_size,) * 2
    assert [p[0] for p in progress] == sorted(p[0] for p in progress)
    assert fake.count("POST", "/upload/thumbnails/set") == 1
    assert "session-secret" not in json.dumps(states)
    # The callback owns each snapshot; later state changes must not mutate earlier ones.
    states[-1]["complete"] = False
    assert result.details["complete"]


def test_dry_run_without_tokens_has_no_http_and_contains_reviewable_bodies(
    fake, upload, monkeypatch
):
    def unexpected(*args, **kwargs):
        pytest.fail("dry-run must not construct a live HTTP client")

    monkeypatch.setattr("reel.publish.youtube.GoogleApiClient", unexpected)
    states = []
    result = YoutubePublisher(
        YoutubeSettings(create_missing_playlists=True),
        None,
        api_url=fake.url + "/api/",
        upload_url=fake.url + "/upload/",
    ).publish(replace(upload, playlist_id=""), None, on_state=states.append)
    assert result.dry_run and result.created and not result.video_id and not result.url
    assert not fake.calls and result.details["file_size"] == upload.video.stat().st_size
    assert result.details["metadata"]["status"]["privacyStatus"] == "unlisted"
    action = result.details["playlist_action"]
    assert (
        action["action"] == "find_or_create"
        and action["body"]["status"]["privacyStatus"] == "unlisted"
    )
    assert result.details["thumbnail_size"] == upload.thumbnail.stat().st_size


def test_live_mode_requires_tokens():
    with pytest.raises(GoogleApiError, match="tokens are required") as error:
        YoutubePublisher(YoutubeSettings(mode="live"), None)
    assert not error.value.retryable and error.value.reason == "missing_tokens"


@pytest.mark.parametrize("long", [False, True])
def test_armenian_utf8_is_intact_with_character_and_byte_limits(fake, upload, long):
    title = "Հայերեն <դաս>" * (20 if long else 1)
    description = "ա" * 2499 + "aբ" if long else upload.description + " <բովանդակություն>"
    value = replace(upload, title=title, description=description)
    publisher(fake).publish(value, None, on_state=lambda state: None)
    call = next(c for c in fake.calls if c["path"] == "/upload/videos")
    snippet = call["body"]["snippet"]
    assert snippet["title"] == title.replace("<", "(").replace(">", ")")[:100]
    assert len(snippet["title"]) <= 100 and len(snippet["description"].encode("utf-8")) <= 5000
    assert "Հայերեն".encode() in call["raw"] and b"\\u0540" not in call["raw"]
    assert "<" not in snippet["description"] and ">" not in snippet["description"]
    if long:
        assert snippet["description"] == "ա" * 2499 + "a"
    else:
        assert snippet["description"] == description.replace("<", "(").replace(">", ")")


@pytest.mark.parametrize("fault", ["cut", "503", "lost_final"])
def test_interrupted_upload_queries_confirmed_offset_and_never_duplicates(fake, upload, fault):
    fake.transfer_faults = [fault]
    waits = []
    result = publisher(fake, sleep=waits.append).publish(upload, None, on_state=lambda state: None)
    assert result.video_id and len(fake.videos) == fake.session_serial == 1
    assert any(c.get("range", "").startswith("bytes */") for c in fake.calls)
    assert bytes(next(iter(fake.sessions.values()))["data"]) == upload.video.read_bytes()
    assert waits and waits[0] == 1
    if fault in {"cut", "503"}:
        chunks = [c for c in fake.calls if c.get("received")]
        assert chunks[1]["range"].startswith(f"bytes {CHUNK_UNIT // 2}-")


@pytest.mark.parametrize("checkpoint", ["session", "chunk", "uploaded", "playlist"])
def test_crash_resumes_from_saved_state_in_a_new_publisher(fake, upload, checkpoint):
    codec, states = TestCodec(), []

    def state(details):
        states.append(details)
        trigger = {
            "session": bool(details.get("session")),
            "chunk": 0 < details.get("upload_offset", 0) < upload.video.stat().st_size,
            "uploaded": bool(details.get("video_id")),
            "playlist": details.get("playlist_done", False),
        }[checkpoint]
        if trigger:
            raise Crash

    with pytest.raises(Crash):
        publisher(fake, codec=codec).publish(upload, None, on_state=state)
    saved = copy.deepcopy(states[-1])
    before = copy.deepcopy(saved)
    result = publisher(fake, codec=codec).publish(upload, saved, on_state=states.append)
    assert result.video_id and len(fake.videos) == fake.session_serial == len(fake.entries) == 1
    assert result.details["complete"] and saved == before
    assert saved.get("complete") is not True
    assert "session-secret" not in json.dumps(states)


@pytest.mark.parametrize("status", ["404", "410"])
def test_expired_session_checks_marker_before_starting_a_new_one(fake, upload, status):
    fake.transfer_faults = [status]
    result = publisher(fake).publish(upload, None, on_state=lambda state: None)
    assert result.video_id and len(fake.videos) == 1 and fake.session_serial == 2
    assert fake.count("GET", "/api/channels") == 2


def test_expired_session_after_commit_finds_the_uploaded_marker(fake, upload):
    fake.transfer_faults = ["expired_complete"]
    result = publisher(fake).publish(upload, None, on_state=lambda state: None)
    assert result.video_id and len(fake.videos) == fake.session_serial == 1
    assert fake.count("GET", "/api/channels") == 2


def test_marker_recovery_requires_both_tags_and_scans_recent_pages(fake, upload):
    fake.page_size = 2
    expected = fake.seed([upload.marker, "reel-file-" + upload.file_sha256[:16]])
    fake.seed([upload.marker, "reel-file-0000000000000000"])
    fake.seed(["different-job", "reel-file-" + upload.file_sha256[:16]])
    fake.seed([])
    result = publisher(fake).publish(upload, None, on_state=lambda state: None)
    assert result.video_id == expected and result.updated and not result.created
    assert fake.session_serial == 0 and fake.count("PUT", "/api/videos") == 1
    history = [
        c
        for c in fake.calls
        if c["path"] == "/api/playlistItems" and c["query"].get("playlistId") == "UU_UPLOADS"
    ]
    assert len(history) == 2


def test_history_error_never_causes_a_blind_upload(fake, upload):
    fake.failures[("GET", "/api/channels")] = [(503, "backendError", "failure", {})]
    with pytest.raises(GoogleApiError):
        publisher(fake).publish(upload, None, on_state=lambda state: None)
    assert fake.session_serial == 0


def test_same_file_rerun_updates_snippet_thumbnail_and_playlist_without_upload(fake, upload):
    first = publisher(fake).publish(upload, None, on_state=lambda state: None)
    second = publisher(fake).publish(
        replace(upload, title="Թարմացված դաս", description="Նոր նկարագրություն"),
        first.details,
        on_state=lambda state: None,
    )
    assert second.video_id == first.video_id and second.updated and not second.created
    assert fake.session_serial == 1 and len(fake.entries) == 1
    assert (
        fake.count("POST", "/api/playlistItems") == 1
        and fake.count("POST", "/upload/thumbnails/set") == 2
    )
    update = next(c for c in fake.calls if c["method"] == "PUT" and c["path"] == "/api/videos")
    assert update["body"]["snippet"]["title"] == "Թարմացված դաս"
    assert update["query"]["part"] == "snippet" and "status" not in update["body"]
    assert "categoryId" in update["body"]["snippet"]


@pytest.mark.parametrize("delete", [False, True])
def test_new_file_replaces_in_playlist_and_optionally_deletes_old_video(fake, upload, delete):
    first = publisher(fake).publish(upload, None, on_state=lambda state: None)
    second = publisher(fake, settings={"delete_replaced_videos": delete}).publish(
        replace(upload, file_sha256="b" * 64),
        first.details,
        on_state=lambda state: None,
    )
    assert second.video_id != first.video_id and second.created and fake.session_serial == 2
    assert list(fake.entries.values()) == [(upload.playlist_id, second.video_id)]
    assert first.video_id in second.replaced_video_ids
    assert (first.video_id not in fake.videos) is delete
    assert (first.video_id in second.details["retained_video_ids"]) is not delete
    add_new = max(
        i
        for i, c in enumerate(fake.calls)
        if c["method"] == "POST" and c["path"] == "/api/playlistItems"
    )
    remove_old = next(
        i
        for i, c in enumerate(fake.calls)
        if c["method"] == "DELETE" and c["path"] == "/api/playlistItems"
    )
    assert add_new < remove_old


def test_replacement_cleanup_survives_a_crash_after_new_video_checkpoint(fake, upload):
    first = publisher(fake).publish(upload, None, on_state=lambda state: None)
    value, states = replace(upload, file_sha256="c" * 64), []

    def state(details):
        states.append(details)
        if details.get("video_id") and details["video_id"] != first.video_id:
            raise Crash

    with pytest.raises(Crash):
        publisher(fake, settings={"delete_replaced_videos": True}).publish(
            value, first.details, on_state=state
        )
    result = publisher(fake, settings={"delete_replaced_videos": True}).publish(
        value, states[-1], on_state=states.append
    )
    assert fake.session_serial == 2 and len(fake.videos) == len(fake.entries) == 1
    assert first.video_id not in fake.videos and result.details["retirements"][0]["deleted"]


def test_private_lock_and_thumbnail_permission_failure_are_flags(fake, upload):
    fake.upload_status["privacyStatus"] = "private"
    fake.failures[("POST", "/upload/thumbnails/set")] = [(403, "forbidden", "not enabled", {})]
    result = publisher(fake).publish(upload, None, on_state=lambda state: None)
    assert set(result.flags) == {"youtube_not_unlisted", "youtube_thumbnail_not_set"}
    assert fake.count("PUT", "/api/videos") == 0
    assert post_bodies(fake, "/upload/videos")[0]["status"]["privacyStatus"] == "unlisted"


@pytest.mark.parametrize("status,reason", [("rejected", "copyright"), ("failed", "codec")])
def test_failed_or_rejected_upload_is_permanent_and_id_was_saved(fake, upload, status, reason):
    fake.upload_status.update(uploadStatus=status, rejectionReason=reason)
    states = []
    with pytest.raises(GoogleApiError, match=reason) as error:
        publisher(fake).publish(upload, None, on_state=states.append)
    assert not error.value.retryable and error.value.reason == "upload_" + status
    assert states[-1]["video_id"] and len(fake.videos) == 1
    assert not fake.entries


def test_no_playlist_flag_when_creation_disabled(fake, upload):
    result = publisher(fake).publish(
        replace(upload, playlist_id=""), None, on_state=lambda state: None
    )
    assert result.playlist_id == "" and "youtube_no_playlist" in result.flags
    assert fake.count("GET", "/api/playlists") == fake.count("POST", "/api/playlists") == 0


def test_find_existing_playlist_by_title_on_later_page(fake, upload):
    fake.page_size = 1
    fake.playlists = {
        "PL_OTHER": {"snippet": {"title": "Other"}},
        "PL_FOUND": {"snippet": {"title": upload.group_name}},
    }
    result = publisher(fake, settings={"create_missing_playlists": True}).publish(
        replace(upload, playlist_id=""),
        None,
        on_state=lambda state: None,
    )
    assert result.playlist_id == "PL_FOUND" and fake.count("POST", "/api/playlists") == 0


def test_cancellation_between_chunks_leaves_resumable_state(fake, upload):
    states, codec = [], TestCodec()

    def check():
        if any(c.get("received") for c in fake.calls):
            raise Crash("cancelled")

    with pytest.raises(Crash, match="cancelled"):
        publisher(fake, codec=codec).publish(upload, None, on_state=states.append, check=check)
    assert states[-1].get("session") and not fake.videos
    result = publisher(fake, codec=codec).publish(upload, states[-1], on_state=states.append)
    assert result.video_id and len(fake.videos) == fake.session_serial == 1


@pytest.mark.parametrize("times", [1, 2])
def test_http_401_invalidates_and_refreshes_only_once(fake, times):
    tokens = Tokens()
    fake.failures[("GET", "/api/channels")] = [(401, "authError", "bad token", {})] * times
    client = GoogleApiClient(tokens, base_url=fake.url + "/api/")
    if times == 2:
        with pytest.raises(GoogleApiError) as error:
            client.request("GET", "channels")
        assert error.value.status == 401 and not error.value.retryable
    else:
        assert client.request("GET", "channels").json()["items"]
    assert tokens.invalidations == 1 and tokens.calls == 2
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer " + TOKEN
    assert fake.calls[1]["headers"]["Authorization"] == "Bearer " + NEW_TOKEN


@pytest.mark.parametrize(
    "status,reason,retryable",
    [
        (403, "quotaExceeded", True),
        (403, "uploadLimitExceeded", True),
        (403, "rateLimitExceeded", True),
        (403, "userRateLimitExceeded", True),
        (400, "backendError", True),
        (429, "unknown", True),
        (503, "unknown", True),
        (403, "forbidden", False),
        (400, "badRequest", False),
        (404, "notFound", False),
    ],
)
def test_google_error_classification(fake, status, reason, retryable):
    fake.failures[("GET", "/api/channels")] = [(status, reason, "untrusted free text", {})]
    with pytest.raises(GoogleApiError) as error:
        GoogleApiClient(Tokens(), base_url=fake.url + "/api/").request("GET", "channels")
    assert error.value.status == status and error.value.retryable is retryable
    if reason in {"quotaExceeded", "uploadLimitExceeded"}:
        assert "daily quota is used up" in str(error.value)


def test_tokens_authorization_and_sessions_never_leak_to_errors_or_logs(
    fake, upload, caplog, monkeypatch
):
    caplog.set_level(logging.DEBUG)
    codec, states = TestCodec(), []
    fake.transfer_faults = ["503"]
    publisher(fake, codec=codec).publish(upload, None, on_state=states.append)
    session = next(value for value in codec.values.values() if value.startswith("http"))
    fake.failures[("GET", "/api/channels")] = [
        (403, session, f"Authorization: Bearer {TOKEN} {session}", {})
    ]
    client = GoogleApiClient(Tokens(), base_url=fake.url + "/api/")
    with pytest.raises(GoogleApiError) as error:
        client.request("GET", "channels")
    text = str(error.value) + repr(error.value) + caplog.text + json.dumps(states)
    assert TOKEN not in text and NEW_TOKEN not in text and session not in text
    assert "Authorization" not in text and "session-secret" not in text

    def broken(*args, **kwargs):
        raise URLError(f"Authorization {TOKEN} {session}")

    monkeypatch.setattr(client._opener, "open", broken)
    with pytest.raises(GoogleApiError) as disconnected:
        client.request("GET", "channels")
    assert disconnected.value.retryable and TOKEN not in str(disconnected.value)
    assert session not in str(disconnected.value)


def test_no_redirect_or_bearer_forwarding_to_untrusted_origin(fake):
    fake.failures[("GET", "/api/channels")] = [
        (302, "", "", {"Location": fake.url + "/api/videos"})
    ]
    tokens = Tokens()
    client = GoogleApiClient(tokens, base_url=fake.url + "/api/")
    with pytest.raises(GoogleApiError) as error:
        client.request("GET", "channels")
    assert error.value.status == 302 and len(fake.calls) == 1
    with pytest.raises(GoogleApiError, match="untrusted origin"):
        client.request("PUT", "http://127.0.0.1:1/private-session?secret=value", data=b"video")
    assert len(fake.calls) == 1 and tokens.calls == 1


@pytest.mark.parametrize("chunk_size", [0, 1, CHUNK_UNIT + 1, True])
def test_chunk_size_must_be_a_multiple_of_256_kib(chunk_size):
    with pytest.raises(GoogleApiError, match="256 KiB"):
        YoutubePublisher(YoutubeSettings(), chunk_size=chunk_size)


@pytest.mark.skipif(os.name != "nt", reason="Windows-only production state protection")
def test_dpapi_session_state_roundtrips_across_codec_instances_without_plaintext():
    url = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=secret-session-capability"
    protected = WindowsSessionCodec().seal(url)
    assert url not in protected and "secret-session" not in protected
    assert WindowsSessionCodec().unseal(protected) == url
    with pytest.raises(GoogleApiError, match="restore"):
        WindowsSessionCodec().unseal("dpapi:invalid-base64!")


def test_stalled_upload_has_bounded_backoff_and_can_resume_later(fake, upload):
    fake.transfer_faults = ["stall"] * 3
    states, waits, codec = [], [], TestCodec()
    with pytest.raises(GoogleApiError, match="retry limit") as error:
        publisher(fake, codec=codec, max_retries=2, sleep=waits.append).publish(
            upload,
            None,
            on_state=states.append,
        )
    assert error.value.retryable and waits == [1, 2]
    assert fake.session_serial == 1 and not fake.videos and states[-1].get("session")
    result = publisher(fake, codec=codec).publish(upload, states[-1], on_state=states.append)
    assert result.video_id and len(fake.videos) == fake.session_serial == 1


def test_invalid_server_range_is_rejected_without_losing_the_session(fake, upload):
    fake.transfer_faults = ["bad_range"]
    states, codec = [], TestCodec()
    with pytest.raises(GoogleApiError, match="invalid upload offset") as error:
        publisher(fake, codec=codec).publish(upload, None, on_state=states.append)
    assert error.value.retryable and states[-1].get("session")
    result = publisher(fake, codec=codec).publish(upload, states[-1], on_state=states.append)
    assert result.video_id and len(fake.videos) == fake.session_serial == 1


def test_thumbnail_quota_is_retryable_not_a_permission_flag(fake, upload):
    fake.failures[("POST", "/upload/thumbnails/set")] = [(403, "quotaExceeded", "quota", {})]
    states = []
    with pytest.raises(GoogleApiError, match="daily quota") as error:
        publisher(fake).publish(upload, None, on_state=states.append)
    assert error.value.retryable and states[-1]["video_id"]
    assert "youtube_thumbnail_not_set" not in states[-1].get("flags", [])
    result = publisher(fake).publish(upload, states[-1], on_state=states.append)
    assert result.video_id and fake.session_serial == 1


def test_created_playlist_checkpoint_prevents_duplicate_creation_after_crash(fake, upload):
    value, states = replace(upload, playlist_id=""), []

    def checkpoint(details):
        states.append(details)
        if details.get("playlist_id"):
            raise Crash

    with pytest.raises(Crash):
        publisher(fake, settings={"create_missing_playlists": True}).publish(
            value,
            None,
            on_state=checkpoint,
        )
    result = publisher(fake, settings={"create_missing_playlists": True}).publish(
        value,
        states[-1],
        on_state=states.append,
    )
    assert result.playlist_id and fake.count("POST", "/api/playlists") == 1
    assert len(fake.entries) == fake.session_serial == 1


def test_disabling_deletion_before_cleanup_keeps_the_replaced_video(fake, upload):
    first = publisher(fake).publish(upload, None, on_state=lambda state: None)
    value, states = replace(upload, file_sha256="d" * 64), []

    def checkpoint(details):
        states.append(details)
        if details.get("video_id") and details["video_id"] != first.video_id:
            raise Crash

    with pytest.raises(Crash):
        publisher(fake, settings={"delete_replaced_videos": True}).publish(
            value,
            first.details,
            on_state=checkpoint,
        )
    result = publisher(fake, settings={"delete_replaced_videos": False}).publish(
        value,
        states[-1],
        on_state=states.append,
    )
    assert first.video_id in fake.videos and first.video_id in result.details["retained_video_ids"]
    assert fake.count("DELETE", "/api/videos") == 0
    assert list(fake.entries.values()) == [(upload.playlist_id, result.video_id)]


def test_incomplete_playlist_lookup_fails_instead_of_creating_duplicate(fake, upload):
    fake.page_size = 1
    fake.playlists = {
        "PL_OTHER": {"snippet": {"title": "Other"}},
        "PL_FOUND": {"snippet": {"title": upload.group_name}},
    }
    with pytest.raises(GoogleApiError, match="page limit") as error:
        publisher(fake, max_pages=1, settings={"create_missing_playlists": True}).publish(
            replace(upload, playlist_id=""),
            None,
            on_state=lambda state: None,
        )
    assert error.value.retryable and fake.count("POST", "/api/playlists") == 0


def test_malformed_history_metadata_never_starts_an_upload(fake, upload):
    fake.seed([["not-a-string-tag"]])
    with pytest.raises(GoogleApiError, match="invalid video metadata") as error:
        publisher(fake).publish(upload, None, on_state=lambda state: None)
    assert error.value.retryable and not fake.sessions


def test_malformed_upload_status_is_retryable_after_saving_video_id(fake, upload):
    fake.upload_status = {"privacyStatus": "unlisted"}
    states = []
    with pytest.raises(GoogleApiError, match="invalid upload status") as error:
        publisher(fake).publish(upload, None, on_state=states.append)
    assert error.value.retryable and states[-1]["video_id"] and len(fake.videos) == 1


@pytest.mark.parametrize(
    "failure", [TimeoutError("fake timeout"), ConnectionResetError("fake reset")]
)
def test_transport_timeouts_and_connection_errors_are_retryable(fake, failure, monkeypatch):
    client = GoogleApiClient(Tokens(), base_url=fake.url + "/api/")

    def broken(*args, **kwargs):
        raise failure

    monkeypatch.setattr(client._opener, "open", broken)
    with pytest.raises(GoogleApiError) as error:
        client.request("GET", "channels")
    assert error.value.retryable and error.value.reason == "connection_error"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only production state protection")
def test_crash_resume_uses_real_dpapi_state_in_a_new_publisher(fake, upload):
    states = []

    def checkpoint(details):
        states.append(details)
        if details.get("session"):
            raise Crash

    with pytest.raises(Crash):
        publisher(fake, codec=WindowsSessionCodec()).publish(upload, None, on_state=checkpoint)
    assert states[-1]["session"].startswith("dpapi:")
    result = publisher(fake, codec=WindowsSessionCodec()).publish(
        upload,
        states[-1],
        on_state=states.append,
    )
    assert result.video_id and len(fake.videos) == fake.session_serial == 1
    assert "upload_id=" not in json.dumps(states)


def test_rerun_checkpoints_are_incomplete_until_all_steps_finish(fake, upload):
    first = publisher(fake).publish(upload, None, on_state=lambda state: None)
    states = []
    fake.failures[("POST", "/upload/thumbnails/set")] = [(503, "backendError", "temporary", {})]
    with pytest.raises(GoogleApiError):
        publisher(fake).publish(upload, first.details, on_state=states.append)
    assert not states[-1]["complete"] and not states[-1]["playlist_done"]
    assert first.details["complete"] and fake.session_serial == 1


def test_dry_run_same_file_previews_the_actual_snippet_update(fake, upload):
    first = publisher(fake).publish(upload, None, on_state=lambda state: None)
    calls = len(fake.calls)
    result = YoutubePublisher(YoutubeSettings()).publish(
        upload,
        first.details,
        on_state=lambda state: None,
    )
    action = result.details["video_action"]
    assert result.dry_run and result.updated and len(fake.calls) == calls
    assert action["method"] == "PUT" and action["part"] == "snippet"
    assert action["body"]["id"] == first.video_id and "status" not in action["body"]


def test_initially_expired_saved_session_checks_marker_before_a_replacement(fake, upload):
    states, codec = [], TestCodec()

    def checkpoint(details):
        states.append(details)
        if details.get("session"):
            raise Crash

    with pytest.raises(Crash):
        publisher(fake, codec=codec).publish(upload, None, on_state=checkpoint)
    fake.sessions.clear()
    result = publisher(fake, codec=codec).publish(upload, states[-1], on_state=states.append)
    assert result.video_id and fake.session_serial == 2 and len(fake.videos) == 1
    assert fake.count("GET", "/api/channels") == 2


# --- T-1018: AES-GCM upload state where DPAPI does not exist (a Linux server) -------------


def test_key_codec_crash_resume_works_in_a_new_publisher_with_the_same_store(fake, upload):
    from reel.auth.store import MemorySecretStore
    from reel.publish.google_api import KeySessionCodec

    store = MemorySecretStore()
    states = []

    def checkpoint(details):
        states.append(details)
        if details.get("session"):
            raise Crash

    with pytest.raises(Crash):
        publisher(fake, codec=KeySessionCodec(store)).publish(upload, None, on_state=checkpoint)
    assert states[-1]["session"].startswith("aesgcm:")
    assert "upload_id" not in states[-1]["session"]
    result = publisher(fake, codec=KeySessionCodec(store)).publish(
        upload, states[-1], on_state=states.append
    )
    assert result.video_id and len(fake.videos) == fake.session_serial == 1
