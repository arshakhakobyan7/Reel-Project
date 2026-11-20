"""Slack publisher on a mocked HTTP layer and loopback server, never real Slack (T-703)."""

import io
import json
import logging
import threading
import traceback
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import date
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request

import pytest

from reel.config import load_config
from reel.config.schema import SlackSettings
from reel.publish.slack import (
    SlackClient,
    SlackError,
    SlackPost,
    SlackPublisher,
    make_slack_publisher,
    render_message,
)

TOKEN = "synthetic-slack-test-token"
CHANNEL = "C0123456789"
TS = "1700000000.000001"
LIVE = SlackSettings(mode="live")


class Response(io.BytesIO):
    def __init__(self, payload, *, content_type="application/json", headers=None):
        raw = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload, ensure_ascii=False).encode()
        )
        super().__init__(raw)
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        for key, value in (headers or {}).items():
            self.headers[key] = value


class FakeSlack:
    def __init__(self):
        self.calls = []
        self.requests = []
        self.messages = []
        self.failures = defaultdict(list)
        self.history_pages = []
        self.drop_post_response = False

    def open(self, request, timeout):
        assert timeout > 0 and request.get_method() == "POST"
        assert request.get_header("Authorization") == f"Bearer {TOKEN}"
        content_type = request.get_header("Content-type").split(";", 1)[0]
        method = request.full_url.rsplit("/", 1)[-1]
        if content_type == "application/json":
            payload = json.loads(request.data.decode("utf-8"))
        else:
            assert content_type == "application/x-www-form-urlencoded"
            fields = parse_qs(
                request.data.decode("ascii"), keep_blank_values=True, strict_parsing=True
            )
            assert all(len(values) == 1 for values in fields.values())
            payload = {key: values[0] for key, values in fields.items()}
            if "include_all_metadata" in payload:
                assert payload["include_all_metadata"] in {"true", "false"}
                payload["include_all_metadata"] = payload["include_all_metadata"] == "true"
            if "limit" in payload:
                assert payload["limit"].isdigit()
                payload["limit"] = int(payload["limit"])
        self.requests.append(request)
        self.calls.append((method, deepcopy(payload)))
        if self.failures[method]:
            failure = self.failures[method].pop(0)
            if isinstance(failure, BaseException):
                raise failure
            return failure if isinstance(failure, Response) else Response(failure)
        if method == "conversations.history":
            if self.history_pages:
                return Response(self.history_pages.pop(0))
            return Response(
                {
                    "ok": True,
                    "messages": [
                        deepcopy(item)
                        for item in reversed(self.messages)
                        if item["channel"] == payload["channel"]
                    ],
                    "has_more": False,
                    "response_metadata": {"next_cursor": ""},
                }
            )
        if method == "chat.postMessage":
            ts = f"1700000000.{len(self.messages) + 1:06d}"
            self.messages.append({**deepcopy(payload), "ts": ts})
            if self.drop_post_response:
                self.drop_post_response = False
                raise TimeoutError("simulated lost response after commit")
            return Response({"ok": True, "channel": payload["channel"], "ts": ts})
        if method == "chat.update":
            target = next(
                (
                    item
                    for item in self.messages
                    if item["ts"] == payload["ts"] and item["channel"] == payload["channel"]
                ),
                None,
            )
            if target is None:
                return Response({"ok": False, "error": "message_not_found"})
            target.update(deepcopy(payload))
            return Response({"ok": True, "channel": payload["channel"], "ts": payload["ts"]})
        if method == "chat.getPermalink":
            return Response(
                {
                    "ok": True,
                    "channel": payload["channel"],
                    "permalink": f"https://demo.slack.com/archives/{payload['channel']}/p"
                    + payload["message_ts"].replace(".", ""),
                }
            )
        pytest.fail("unexpected fake Slack method")

    def client(self):
        client = SlackClient(TOKEN)
        client._opener = self
        return client

    def count(self, method):
        return sum(name == method for name, _ in self.calls)


@pytest.fixture
def api():
    return FakeSlack()


@pytest.fixture
def post():
    return SlackPost(
        job_id=7,
        channel=CHANNEL,
        title="Դաս 7",
        url="https://video.example/watch?v=7&a=1",
        summary="Բառարաններ և օրինակներ",
        group="Python 12",
        lesson=7,
        date=date(2026, 11, 19),
        duration=123.5,
    )


def publisher(api, **kwargs):
    return SlackPublisher(LIVE, client=api.client(), **kwargs)


def test_one_message_per_job_across_retries_with_and_without_saved_id(api, post):
    first = publisher(api).publish(post, None)
    second = publisher(api).publish(post, None)  # New process, no saved result: recover metadata.
    third = publisher(api).publish(replace(post, title="Թարմացված դաս"), second.external_id)
    assert first.created and not first.updated and not first.dry_run
    assert second.updated and third.updated and not third.created
    assert first.external_id == second.external_id == third.external_id == f"{CHANNEL}:{TS}"
    assert len(api.messages) == api.count("chat.postMessage") == 1
    assert api.count("chat.update") == 2
    assert "Թարմացված դաս" in api.messages[0]["text"]
    assert api.messages[0]["metadata"] == {
        "event_type": "reel_video",
        "event_payload": {"job_id": 7},
    }
    assert all(
        payload["include_all_metadata"] is True
        for name, payload in api.calls
        if name == "conversations.history"
    )


def test_crash_after_post_before_response_is_recovered_by_history_metadata(api, post):
    api.drop_post_response = True
    with pytest.raises(SlackError) as error:
        publisher(api).publish(post, None)
    assert error.value.retryable and len(api.messages) == 1
    result = publisher(api).publish(post, None)
    assert result.updated and result.external_id == f"{CHANNEL}:{TS}"
    assert len(api.messages) == api.count("chat.postMessage") == 1


def test_permalink_is_returned_for_storage(api, post):
    result = publisher(api).publish(post, None)
    assert result.url == f"https://demo.slack.com/archives/{CHANNEL}/p{TS.replace('.', '')}"
    assert api.calls[-1] == ("chat.getPermalink", {"channel": CHANNEL, "message_ts": TS})


def test_saved_id_updates_without_history_lookup(api, post):
    first = publisher(api).publish(post, None)
    api.calls.clear()
    result = publisher(api).publish(post, first.external_id)
    assert result.updated and [name for name, _ in api.calls] == [
        "chat.update",
        "chat.getPermalink",
    ]
    assert api.calls[0][1]["metadata"] == post.metadata()


def test_permalink_failure_carries_committed_id_and_retry_does_not_duplicate(api, post):
    api.failures["chat.getPermalink"].append({"ok": False, "error": "internal_error"})
    with pytest.raises(SlackError) as error:
        publisher(api).publish(post, None)
    assert error.value.retryable and error.value.external_id == f"{CHANNEL}:{TS}"
    result = publisher(api).publish(post, None)
    assert result.updated and result.url and len(api.messages) == 1


@pytest.mark.parametrize("previous", [None, f"{CHANNEL}:{TS}"])
def test_dry_run_renders_without_any_http_calls(api, post, previous, caplog):
    caplog.set_level(logging.INFO, logger="reel.publish.slack")
    settings = SlackSettings(message_template="{title}\n{url}")
    result = SlackPublisher(settings, client=api.client()).publish(post, previous)
    assert result.dry_run and not result.created and not result.updated
    assert result.external_id is result.url is None
    assert result.text == "Դաս 7\nhttps://video.example/watch?v=7&amp;a=1"
    assert api.calls == [] and "Dry-run" in caplog.text and TOKEN not in caplog.text


def test_dry_run_does_not_need_token_or_configured_channel(post):
    result = SlackPublisher(SlackSettings()).publish(replace(post, channel=""), None)
    assert result.dry_run and "Դաս 7" in result.text


def test_live_without_token_is_clear_permanent_error(post):
    with pytest.raises(SlackError, match="SLACK_BOT_TOKEN") as error:
        SlackPublisher(LIVE).publish(post, None)
    assert not error.value.retryable and error.value.code == "missing_token"


def test_field_values_are_escaped_after_formatting_and_literal_markup_is_preserved(post):
    modified = replace(post, title="A&B <@U123> >", summary="<!channel>")
    text = render_message(
        "*{title}* {summary} <{url}|Դիտել> {lesson:03d} {duration:.1f} {date:%Y}", modified
    )
    assert text == (
        "*A&amp;B &lt;@U123&gt; &gt;* &lt;!channel&gt; "
        "<https://video.example/watch?v=7&amp;a=1|Դիտել> 007 123.5 2026"
    )
    assert render_message("{{literal}} {group}", post) == "{literal} Python 12"


@pytest.mark.parametrize(
    "template",
    [
        "{unknown}",
        "{title.__class__}",
        "{title[0]}",
        "{lesson:{duration}}",
        "{lesson:bad}",
        "{",
        "   ",
    ],
)
def test_bad_templates_are_permanent_without_exposing_field_values(post, template):
    with pytest.raises(SlackError) as error:
        render_message(template, post)
    assert not error.value.retryable and post.title not in str(error.value)


@pytest.mark.parametrize("job_id", [0, -1, True, "7"])
def test_job_id_must_be_positive_integer(post, job_id):
    with pytest.raises(SlackError) as error:
        replace(post, job_id=job_id)
    assert not error.value.retryable


def test_all_template_fields_and_armenian_are_sent_as_utf8_json(api, post):
    settings = SlackSettings(
        mode="live", message_template="{title}|{summary}|{url}|{group}|{lesson}|{date}|{duration}"
    )
    result = SlackPublisher(settings, client=api.client()).publish(post, None)
    request = next(
        request for request in api.requests if request.full_url.endswith("chat.postMessage")
    )
    assert "Դաս 7".encode() in request.data and b"\\u0534" not in request.data
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["text"] == result.text and "Բառարաններ և օրինակներ" in payload["text"]
    assert payload["parse"] == "none" and payload["unfurl_links"] is False


def test_recovery_follows_history_cursors(api, post):
    api.messages.append({"channel": CHANNEL, "ts": TS, "text": "old", "metadata": post.metadata()})
    api.history_pages = [
        {
            "ok": True,
            "messages": [{"ts": "1700000001.000001", "text": "unrelated"}],
            "has_more": True,
            "response_metadata": {"next_cursor": "page-2"},
        },
        {"ok": True, "messages": [deepcopy(api.messages[0])], "has_more": False},
    ]
    result = publisher(api).publish(post, None)
    assert result.updated and api.count("chat.postMessage") == 0
    history = [payload for method, payload in api.calls if method == "conversations.history"]
    assert "cursor" not in history[0] and history[1]["cursor"] == "page-2"
    assert all(payload["include_all_metadata"] for payload in history)


@pytest.mark.parametrize(
    "page",
    [
        {"ok": True, "messages": [], "has_more": True},
        {"ok": True, "messages": "invalid"},
        {"ok": True, "messages": ["invalid"]},
        {"ok": True, "messages": [{"metadata": []}]},
        {"ok": True, "messages": [{"metadata": {"event_type": "reel_video", "event_payload": []}}]},
        {"ok": True, "messages": [], "has_more": "false"},
        {"ok": True, "messages": [], "response_metadata": {"next_cursor": 17}},
    ],
)
def test_unverifiable_history_never_triggers_a_new_post(api, post, page):
    api.history_pages = [page]
    with pytest.raises(SlackError) as error:
        publisher(api).publish(post, None)
    assert error.value.retryable and api.count("chat.postMessage") == 0


def test_history_page_bound_and_cursor_cycles_fail_closed(api, post):
    page = {
        "ok": True,
        "messages": [],
        "has_more": True,
        "response_metadata": {"next_cursor": "same-page"},
    }
    api.history_pages = [page, page]
    with pytest.raises(SlackError, match="history_incomplete"):
        publisher(api, max_history_pages=1).publish(post, None)
    api.history_pages = [page, page]
    with pytest.raises(SlackError, match="history_incomplete"):
        publisher(api).publish(post, None)
    assert api.count("chat.postMessage") == 0


def test_instance_namespace_prevents_job_id_collision_and_is_recovered(api, post):
    first = publisher(api).publish(replace(post, instance_id="database-A"), None)
    second_post = replace(post, instance_id="database-B")
    second = publisher(api).publish(second_post, None)
    repeated = publisher(api).publish(second_post, None)
    assert first.external_id != second.external_id == repeated.external_id
    assert repeated.updated and len(api.messages) == 2
    assert api.messages[-1]["metadata"]["event_payload"]["instance_id"] == "database-B"


def test_history_matches_string_job_id_but_not_boolean(api, post):
    api.messages = [
        {
            "channel": CHANNEL,
            "ts": "1700000000.000001",
            "metadata": {"event_type": "reel_video", "event_payload": {"job_id": "7"}},
        },
        {
            "channel": CHANNEL,
            "ts": "1700000000.000002",
            "metadata": {"event_type": "reel_video", "event_payload": {"job_id": True}},
        },
    ]
    result = publisher(api).publish(post, None)
    assert result.external_id == f"{CHANNEL}:{TS}" and api.count("chat.postMessage") == 0


@pytest.mark.parametrize("previous", ["bad", "C012:NaN", "C012:1.0:extra"])
def test_invalid_saved_id_fails_without_posting(api, post, previous):
    with pytest.raises(SlackError) as error:
        publisher(api).publish(post, previous)
    assert not error.value.retryable and api.calls == []


def test_changed_channel_is_not_silently_posted_again(api, post):
    with pytest.raises(SlackError, match="previous_channel_mismatch"):
        publisher(api).publish(post, f"C_OTHER:{TS}")
    assert api.calls == []


def test_deleted_saved_message_is_permanent_without_fallback_post(api, post):
    with pytest.raises(SlackError, match="message_not_found") as error:
        publisher(api).publish(post, f"{CHANNEL}:{TS}")
    assert not error.value.retryable and api.count("chat.postMessage") == 0


@pytest.mark.parametrize(
    "code,retryable",
    [
        ("ratelimited", True),
        ("internal_error", True),
        ("service_unavailable", True),
        ("fatal_error", True),
        ("request_timeout", True),
        ("invalid_auth", False),
        ("channel_not_found", False),
        ("not_in_channel", False),
        ("missing_scope", False),
        ("msg_too_long", False),
    ],
)
def test_api_errors_are_classified_without_leaking_token(api, code, retryable, caplog):
    api.failures["conversations.history"].append(
        Response({"ok": False, "error": code, "detail": TOKEN}, headers={"Retry-After": "4"})
    )
    with pytest.raises(SlackError) as error:
        api.client().history(CHANNEL)
    assert error.value.code == code and error.value.retryable is retryable
    assert error.value.retry_after == 4
    assert TOKEN not in str(error.value) + caplog.text


def http_error(status, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(
        "https://slack.invalid/api/conversations.history",
        status,
        TOKEN,
        headers,
        io.BytesIO(TOKEN.encode()),
    )


@pytest.mark.parametrize(
    "status,retryable",
    [(429, True), (500, True), (503, True), (400, False), (401, False), (403, False)],
)
def test_http_errors_and_retry_after_are_classified(api, status, retryable):
    api.failures["conversations.history"].append(http_error(status, "3"))
    with pytest.raises(SlackError) as error:
        api.client().history(CHANNEL)
    assert error.value.retryable is retryable and error.value.retry_after == 3
    assert TOKEN not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("header", [None, "bad", "-1", "nan", "inf"])
def test_invalid_retry_after_is_not_invented(api, header):
    api.failures["conversations.history"].append(http_error(429, header))
    with pytest.raises(SlackError) as error:
        api.client().history(CHANNEL)
    assert error.value.retryable and error.value.retry_after is None


@pytest.mark.parametrize("failure", [TimeoutError(TOKEN), URLError(TOKEN), OSError(TOKEN)])
def test_connection_failures_are_retryable_and_sanitised(api, failure):
    api.failures["conversations.history"].append(failure)
    with pytest.raises(SlackError) as error:
        api.client().history(CHANNEL)
    assert error.value.retryable and TOKEN not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        b"\xff",
        {"ok": "true"},
        [],
        {"ok": False, "error": TOKEN},
        b'{"ok":true,"ok":false}',
        b'{"ok":true,"value":NaN}',
    ],
)
def test_malformed_or_unknown_errors_never_expose_response_data(api, payload):
    api.failures["conversations.history"].append(Response(payload))
    with pytest.raises(SlackError) as error:
        api.client().history(CHANNEL)
    assert TOKEN not in str(error.value)


def test_non_json_response_is_retryable(api):
    api.failures["conversations.history"].append(
        Response(b"<html>error</html>", content_type="text/html")
    )
    with pytest.raises(SlackError) as error:
        api.client().history(CHANNEL)
    assert error.value.retryable


@pytest.mark.parametrize(
    "permalink",
    [None, "javascript:alert(1)", "http://insecure.invalid", "https://user:secret@demo.slack.com"],
)
def test_bad_permalink_never_returns_fake_success(api, post, permalink):
    api.failures["chat.getPermalink"].append({"ok": True, "permalink": permalink})
    with pytest.raises(SlackError) as error:
        publisher(api).publish(post, None)
    assert error.value.retryable and error.value.external_id == f"{CHANNEL}:{TS}"


def test_factory_uses_mode_template_and_secret_from_config(project, write_config):
    write_config('[slack]\nmessage_template="Դիտել {title}"\n')
    cfg = load_config(project / "config" / "reel.toml", environ={"SLACK_BOT_TOKEN": TOKEN})
    post = SlackPost(1, "", "Դաս", "https://video.example/1")
    result = make_slack_publisher(cfg).publish(post, None)
    assert result.dry_run and result.text == "Դիտել Դաս"


@pytest.fixture
def loopback():
    api = FakeSlack()
    observations = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            observations.append(
                (self.path, raw, self.headers.get("Authorization") == f"Bearer {TOKEN}")
            )
            request = Request(
                "https://slack.invalid" + self.path,
                data=raw,
                method="POST",
                headers={
                    "Authorization": self.headers.get("Authorization"),
                    "Content-Type": self.headers.get("Content-Type"),
                },
            )
            with api.open(request, 1) as response:
                body = response.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/", api, observations
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_real_urllib_transport_on_loopback_uses_method_specific_post_bodies_and_intact_utf8(
    loopback, post
):
    base_url, api, observations = loopback
    client = SlackClient(TOKEN, base_url=base_url, timeout_seconds=1)
    first = SlackPublisher(LIVE, client=client).publish(post, None)
    second = SlackPublisher(LIVE, client=client).publish(post, first.external_id)
    assert first.created and second.updated and len(api.messages) == 1
    assert all(authenticated for _, _, authenticated in observations)
    posted = next(raw for path, raw, _ in observations if path.endswith("chat.postMessage"))
    assert "Դաս 7".encode() in posted
    assert {path.rsplit("/", 1)[-1] for path, _, _ in observations} == {
        "conversations.history",
        "chat.postMessage",
        "chat.update",
        "chat.getPermalink",
    }


def test_content_type_used_per_method_on_loopback(loopback, post):
    base_url, api, _ = loopback
    client = SlackClient(TOKEN, base_url=base_url, timeout_seconds=1)
    publisher = SlackPublisher(LIVE, client=client)
    first = publisher.publish(post, None)
    publisher.publish(post, first.external_id)
    expected = {
        "chat.postMessage": "application/json",
        "chat.update": "application/json",
        "chat.getPermalink": "application/x-www-form-urlencoded",
        "conversations.history": "application/x-www-form-urlencoded",
    }
    seen = set()
    for request in api.requests:
        method = request.full_url.rsplit("/", 1)[-1]
        seen.add(method)
        assert request.get_method() == "POST"
        assert request.get_header("Authorization") == f"Bearer {TOKEN}"
        assert request.get_header("Content-type").split(";", 1)[0] == expected[method]
        if method == "conversations.history":
            fields = parse_qs(request.data.decode("ascii"))
            assert fields["channel"] == [CHANNEL]
            assert fields["include_all_metadata"] == ["true"]
            assert fields["limit"] == ["100"]
        elif method == "chat.getPermalink":
            fields = parse_qs(request.data.decode("ascii"))
            assert fields == {"channel": [CHANNEL], "message_ts": [TS]}
    assert seen == set(expected)


def test_form_encoding_uses_lowercase_false_and_text_numbers(api):
    client = api.client()
    client._call(
        "conversations.history",
        {
            "channel": CHANNEL,
            "include_all_metadata": False,
            "limit": 15,
        },
    )
    assert parse_qs(api.requests[-1].data.decode("ascii")) == {
        "channel": [CHANNEL],
        "include_all_metadata": ["false"],
        "limit": ["15"],
    }
    assert api.calls[-1][1]["include_all_metadata"] is False
    assert api.calls[-1][1]["limit"] == 15


@pytest.mark.parametrize(
    "channel",
    [
        "#python-12",
        "python-12",
        "c0123456789",
        "U0123456789",
        "C1234567",
        "C0123456_9",
        " C0123456789",
        "C0123456789\n",
        "",
        None,
    ],
)
def test_invalid_channel_is_permanent_and_never_calls_http(api, post, channel):
    with pytest.raises(SlackError) as error:
        publisher(api).publish(replace(post, channel=channel), None)
    assert error.value.code == "invalid_channel" and not error.value.retryable
    assert "channel ID" in str(error.value) and "not the channel name" in str(error.value)
    assert "#python-12" in str(error.value)
    assert api.calls == api.requests == []


@pytest.mark.parametrize("method", ["post_message", "update_message", "get_permalink", "history"])
def test_direct_client_methods_reject_channel_names_before_http(api, method):
    client = api.client()
    arguments = {
        "post_message": ("#python-12", "text", {}),
        "update_message": ("#python-12", TS, "text", {}),
        "get_permalink": ("#python-12", TS),
        "history": ("#python-12",),
    }
    with pytest.raises(SlackError) as error:
        getattr(client, method)(*arguments[method])
    assert error.value.code == "invalid_channel" and not error.value.retryable
    assert "channel ID" in str(error.value) and api.calls == api.requests == []


@pytest.mark.parametrize("channel", ["C01234567", "G01234567", "D01234567"])
def test_all_supported_channel_id_prefixes_are_accepted(api, post, channel):
    result = publisher(api).publish(replace(post, channel=channel), None)
    assert result.created and result.external_id == f"{channel}:{TS}"
    assert len(api.messages) == 1
