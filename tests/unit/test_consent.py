"""Consent adapters and contract checks using synthetic registries and loopback (T-601)."""

import json
import logging
import time
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest

from reel.config import load_config
from reel.consent import ConsentProvider, StudentStatus, make_consent_provider
from reel.consent.file_provider import FileConsentProvider
from reel.consent.http_provider import HttpConsentProvider
from reel.consent.mock_server import ConsentMockServer
from reel.consent.provider import MAX_JSON_BYTES
from reel.db import ConsentResult

TOKEN = "synthetic-consent-test-token"
GROUP = "Դիզայն 3 / A&B + Python"
LESSON_DATE = date(2026, 11, 19)


def record(*statuses):
    return {
        "group_ref": "g-1",
        "students": [
            {"id": f"s-{index}", "status": status} for index, status in enumerate(statuses)
        ],
    }


def write_registry(path: Path, value) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def client(server: ConsentMockServer, **kwargs) -> HttpConsentProvider:
    return HttpConsentProvider(server.url, token=TOKEN, **kwargs)


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (("granted", "granted"), ConsentResult.PASSED),
        (("granted", "denied"), ConsentResult.OPTED_OUT),
        (("granted", "unknown"), ConsentResult.OPTED_OUT),
        ((None,), ConsentResult.OPTED_OUT),
        (("pending",), ConsentResult.OPTED_OUT),
        (("GRANTED",), ConsentResult.OPTED_OUT),
        ((), ConsentResult.UNKNOWN),
    ],
)
@pytest.mark.parametrize("backend", ["file", "http"])
def test_status_mapping_for_both_providers(tmp_path, statuses, expected, backend):
    entry = record(*statuses)
    if backend == "file":
        provider = FileConsentProvider(write_registry(tmp_path / "registry.json", {GROUP: entry}))
        result = provider.check(GROUP, LESSON_DATE)
    else:
        with ConsentMockServer({GROUP: entry}, token=TOKEN) as server:
            result = client(server).check(GROUP, LESSON_DATE)
    assert result.result is expected
    assert result.group_ref == "g-1" and result.message
    assert all(set(student) == {"id", "status"} for student in result.students_json())
    assert len(result.students) == len(statuses)


@pytest.mark.parametrize("backend", ["file", "http"])
def test_missing_student_status_is_opted_out(tmp_path, backend):
    entry = {"students": [{"id": "s-1"}], "group_ref": "g-1"}
    if backend == "file":
        result = FileConsentProvider(
            write_registry(tmp_path / "registry.json", {"Group": entry})
        ).check("Group", None)
    else:
        with ConsentMockServer({"Group": entry}, token=TOKEN) as server:
            result = client(server).check("Group", None)
    assert result.result is ConsentResult.OPTED_OUT
    assert result.students_json() == [{"id": "s-1", "status": "unknown"}]
    assert result.students[0].status is StudentStatus.UNKNOWN


def test_file_lookup_is_unicode_case_insensitive_and_uses_group_name_as_default_ref(tmp_path):
    name = "Դիզայն 3"
    entry = {"students": [{"id": "s-1", "status": "granted"}]}
    provider = FileConsentProvider(write_registry(tmp_path / "registry.json", {name: entry}))
    decision = provider.check("  " + name.upper() + "  ", None)
    assert decision.result is ConsentResult.PASSED and decision.group_ref == name
    assert provider.name == "file" and isinstance(provider, ConsentProvider)


def test_file_is_reread_after_staff_edits(tmp_path):
    path = write_registry(tmp_path / "registry.json", {"Group": record("granted")})
    provider = FileConsentProvider(path)
    assert provider.check("Group", None).result is ConsentResult.PASSED
    write_registry(path, {"Group": record("denied")})
    assert provider.check("Group", None).result is ConsentResult.OPTED_OUT
    path.write_text("{", encoding="utf-8")
    assert provider.check("Group", None).result is ConsentResult.UNREACHABLE
    write_registry(path, {"Group": record("granted")})
    assert provider.check("Group", None).result is ConsentResult.PASSED


def test_file_group_missing_is_unknown(tmp_path):
    provider = FileConsentProvider(
        write_registry(tmp_path / "registry.json", {"Other": record("granted")})
    )
    result = provider.check("Group", None)
    assert result.result is ConsentResult.UNKNOWN
    assert result.group_ref is None and result.students == ()


def test_file_missing_is_clear_unreachable_result(tmp_path):
    result = FileConsentProvider(tmp_path / "absent.json").check("Group", None)
    assert result.result is ConsentResult.UNREACHABLE and "missing" in result.message


@pytest.mark.parametrize("raw", [b"{", b"\xff", b'{"Group":NaN}', b'{"Group":{},"Group":{}}'])
def test_invalid_file_json_is_unreachable(tmp_path, raw):
    path = tmp_path / "registry.json"
    path.write_bytes(raw)
    result = FileConsentProvider(path).check("Group", None)
    assert result.result is ConsentResult.UNREACHABLE and "JSON" in result.message


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"Group": []},
        {"Group": {"students": "all"}},
        {"Group": {"students": [], "email": "private@example.invalid"}},
        {"Group": record("granted"), "GROUP": record("denied")},
        {"Group": record("granted"), "Broken": {}},
    ],
)
def test_invalid_registry_structure_never_approves(tmp_path, value):
    result = FileConsentProvider(write_registry(tmp_path / "registry.json", value)).check(
        "Group", None
    )
    assert result.result is ConsentResult.UNREACHABLE
    assert result.students_json() == [] and "private@example.invalid" not in result.message


def test_file_read_error_is_sanitised(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError(TOKEN)

    monkeypatch.setattr(Path, "open", fail)
    result = FileConsentProvider(tmp_path / "registry.json").check("Group", None)
    assert result.result is ConsentResult.UNREACHABLE
    assert "cannot be read" in result.message and TOKEN not in result.message


def test_file_accepts_utf8_bom_and_rejects_oversized_body(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"Group": record("granted")}), encoding="utf-8-sig")
    provider = FileConsentProvider(path)
    assert provider.check("Group", None).result is ConsentResult.PASSED
    path.write_bytes(b" " * (MAX_JSON_BYTES + 1))
    assert provider.check("Group", None).result is ConsentResult.UNREACHABLE


def test_decision_student_entries_are_frozen_and_json_copy_cannot_change_evidence(tmp_path):
    provider = FileConsentProvider(
        write_registry(tmp_path / "registry.json", {"Group": record("granted")})
    )
    result = provider.check("Group", None)
    with pytest.raises(FrozenInstanceError):
        result.students[0].status = StudentStatus.DENIED
    serialised = result.students_json()
    serialised[0]["status"] = "denied"
    assert result.students_json()[0]["status"] == "granted"


def test_http_encodes_group_date_and_authenticates_without_retaining_token():
    with ConsentMockServer({GROUP: record("granted")}, token=TOKEN) as server:
        provider = client(server)
        assert provider.check(GROUP, LESSON_DATE).result is ConsentResult.PASSED
        assert provider.check(GROUP, None).result is ConsentResult.PASSED
        assert server.requests == [{"group": GROUP, "lesson_date": "2026-11-19"}, {"group": GROUP}]
        assert server.request_count == 2 and provider.name == "http"
        assert TOKEN not in repr(server.requests) + repr(provider)


def test_http_unknown_group_against_mock():
    with ConsentMockServer({}, token=TOKEN) as server:
        result = client(server).check("Absent", None)
    assert result.result is ConsentResult.UNKNOWN
    assert result.group_ref is None and result.students == ()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
def test_http_errors_are_unreachable_and_do_not_expose_response_bodies(status, caplog, capsys):
    with ConsentMockServer(
        {"Group": record("granted")},
        token=TOKEN,
        status=status,
        raw_body=json.dumps({"error": TOKEN, "email": "private@example.invalid"}).encode(),
    ) as server:
        result = client(server).check("Group", None)
    assert result.result is ConsentResult.UNREACHABLE
    assert str(status) in result.message
    if status in {401, 403}:
        assert "authentication" in result.message
    output = capsys.readouterr()
    assert TOKEN not in result.message + repr(result) + caplog.text + output.out + output.err
    assert "private@example.invalid" not in result.message


def test_wrong_bearer_token_is_rejected():
    with ConsentMockServer({"Group": record("granted")}, token=TOKEN) as server:
        result = HttpConsentProvider(server.url, token="wrong-synthetic-token").check("Group", None)
    assert result.result is ConsentResult.UNREACHABLE and "401" in result.message


@pytest.mark.parametrize("token", [None, "", "bad\r\nheader", "bad token", "ոչ-ascii"])
def test_missing_or_invalid_token_makes_no_request(token):
    with ConsentMockServer({"Group": record("granted")}, token=TOKEN) as server:
        result = HttpConsentProvider(server.url, token=token).check("Group", None)
        assert server.request_count == 0
    assert result.result is ConsentResult.UNREACHABLE
    assert "CORE_ENGINE_API_TOKEN" in result.message


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"\xff",
        b'{"group":NaN}',
        b'{"group":"Group","group":"Other","group_ref":"g-1","students":[]}',
    ],
)
def test_http_malformed_json_is_unreachable(raw):
    with ConsentMockServer({"Group": record("granted")}, token=TOKEN, raw_body=raw) as server:
        result = client(server).check("Group", None)
    assert result.result is ConsentResult.UNREACHABLE


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"group": "Group", "group_ref": "g-1"},
        {"group": "Other", "group_ref": "g-1", "students": [{"id": "s-1", "status": "granted"}]},
        {"group": "Group", "group_ref": 1, "students": []},
        {"group": "Group", "group_ref": "", "students": []},
        {"group": "Group", "group_ref": "g-1", "students": "all granted"},
        {"group": "Group", "group_ref": "g-1", "students": [{"status": "granted"}]},
        {"group": "Group", "group_ref": "g-1", "students": [{"id": "", "status": "granted"}]},
        {"group": "Group", "group_ref": "g-1", "students": [{"id": 7, "status": "granted"}]},
        {"group": "Group", "group_ref": "g-1", "students": [{"id": "s-1", "status": True}]},
        {"group": "Group", "group_ref": "g-1", "students": [{"id": "s-1", "status": []}]},
        {
            "group": "Group",
            "group_ref": "g-1",
            "students": [{"id": "s-1", "status": "granted"}, {"id": "s-1", "status": "denied"}],
        },
        {"group": "Group", "group_ref": "g-1", "students": [], "passed": True},
        {
            "group": "Group",
            "group_ref": "g-1",
            "students": [{"id": "s-1", "status": "granted", "email": "private@example.invalid"}],
        },
    ],
)
def test_http_strict_shape_validation_never_approves_malformed_evidence(payload):
    with ConsentMockServer(
        {"Group": record("granted")}, token=TOKEN, raw_body=json.dumps(payload).encode()
    ) as server:
        result = client(server).check("Group", None)
    assert result.result is ConsentResult.UNREACHABLE
    assert result.students_json() == [] and "private@example.invalid" not in result.message


@pytest.mark.parametrize(
    "content_type", ["text/html", "text/plain", "application/json; charset=latin-1"]
)
def test_http_only_accepts_utf8_json(content_type):
    with ConsentMockServer(
        {"Group": record("granted")}, token=TOKEN, content_type=content_type
    ) as server:
        assert client(server).check("Group", None).result is ConsentResult.UNREACHABLE


def test_http_oversized_body_is_unreachable():
    with ConsentMockServer(
        {"Group": record("granted")}, token=TOKEN, raw_body=b" " * (MAX_JSON_BYTES + 1)
    ) as server:
        assert client(server).check("Group", None).result is ConsentResult.UNREACHABLE


def test_http_timeout_is_fast_and_unreachable():
    with ConsentMockServer({"Group": record("granted")}, token=TOKEN, delay_seconds=0.2) as server:
        started = time.monotonic()
        result = client(server, timeout_seconds=0.02).check("Group", None)
        assert time.monotonic() - started < 1
    assert result.result is ConsentResult.UNREACHABLE and "timed out" in result.message


def test_http_connection_failure_is_sanitised():
    with ConsentMockServer() as server:
        url = server.url
    result = HttpConsentProvider(url, token=TOKEN, timeout_seconds=0.02).check("Group", None)
    assert result.result is ConsentResult.UNREACHABLE and TOKEN not in result.message


def test_http_redirects_are_not_followed():
    with ConsentMockServer({"Group": record("granted")}, token=TOKEN) as target:
        with ConsentMockServer(
            token=TOKEN, status=302, headers={"Location": target.url + "/v1/consent?group=Group"}
        ) as source:
            result = client(source).check("Group", None)
        assert target.request_count == 0
    assert result.result is ConsentResult.UNREACHABLE


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///private",
        "http://user:password@127.0.0.1",
        "http://127.0.0.1?token=private",
        "http://127.0.0.1#private",
        "http://127.0.0.1:invalid",
        "http://127.0.0.1/\nprivate",
    ],
)
def test_invalid_endpoint_is_rejected_without_exposing_configuration(base_url):
    result = HttpConsentProvider(base_url, token=TOKEN).check("Group", None)
    assert result.result is ConsentResult.UNREACHABLE
    assert "configuration" in result.message and "private" not in result.message


def test_dry_run_records_intent_without_network_or_fake_approval(caplog):
    caplog.set_level(logging.INFO, logger="reel.consent")
    with ConsentMockServer({"Group": record("granted")}, token=TOKEN) as server:
        result = HttpConsentProvider(server.url, token=TOKEN, dry_run=True).check(
            "Group", LESSON_DATE
        )
        assert server.request_count == 0
    assert result.result is ConsentResult.UNREACHABLE and result.students == ()
    assert "Dry-run" in result.message and "GET /v1/consent?group=Group" in caplog.text
    assert "lesson_date=2026-11-19" in result.message and TOKEN not in result.message + caplog.text


def test_factory_uses_resolved_file_path_from_config(project, write_config):
    write_config('[consent]\nprovider="file"\nfile_path="config/custom-consent.json"\n')
    cfg = load_config(project / "config" / "reel.toml", environ={})
    write_registry(cfg.paths.consent_file, {"Group": record("granted")})
    provider = make_consent_provider(cfg)
    assert isinstance(provider, FileConsentProvider) and provider.path == cfg.paths.consent_file
    assert provider.check("Group", None).result is ConsentResult.PASSED


def test_factory_uses_http_url_token_and_timeout_from_config(project, write_config):
    with ConsentMockServer({"Group": record("granted")}, token=TOKEN, delay_seconds=0.2) as server:
        write_config(
            f'[consent]\nprovider="http"\nhttp_base_url="{server.url}"\ntimeout_seconds=0.02\n'
        )
        cfg = load_config(
            project / "config" / "reel.toml", environ={"CORE_ENGINE_API_TOKEN": TOKEN}
        )
        provider = make_consent_provider(cfg)
        assert isinstance(provider, HttpConsentProvider)
        result = provider.check("Group", None)
        assert server.request_count == 1
        assert result.result is ConsentResult.UNREACHABLE and "timed out" in result.message


def test_factory_can_dry_run_without_a_token(project, write_config):
    with ConsentMockServer({"Group": record("granted")}) as server:
        write_config(f'[consent]\nprovider="http"\nhttp_base_url="{server.url}"\n')
        cfg = load_config(project / "config" / "reel.toml", environ={})
        result = make_consent_provider(cfg, dry_run=True).check("Group", None)
        assert server.request_count == 0
    assert result.result is ConsentResult.UNREACHABLE and "Dry-run" in result.message


def test_example_registry_is_valid_and_conservative():
    example = Path(__file__).resolve().parents[2] / "config" / "consent.example.json"
    result = FileConsentProvider(example).check("Example Group", None)
    assert result.result is ConsentResult.OPTED_OUT
    assert len(result.students) == 2 and all(
        item.id.startswith("demo-") for item in result.students
    )
