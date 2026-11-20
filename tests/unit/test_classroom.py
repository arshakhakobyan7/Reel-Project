"""Classroom publishing with a loopback fake, fake credentials and Armenian text (T-704)."""

import copy
import json
import logging
from dataclasses import replace
from datetime import date

import pytest
from classroom_fake import ClassroomFake

from reel.config.schema import ClassroomSettings
from reel.publish.classroom import ClassroomPost, ClassroomPublisher, render_text
from reel.publish.google_api import GoogleApiError

TOKEN = "fake-classroom-access-token-for-tests"
REFRESHED = "refreshed-classroom-access-token-for-tests"
KINDS = [("announcement", "announcements"), ("material", "courseWorkMaterials")]


class Tokens:
    def __init__(self):
        self.invalidations = 0

    def token(self):
        return REFRESHED if self.invalidations else TOKEN

    def invalidate(self):
        self.invalidations += 1


class Crash(Exception):
    """A process or stage stopped at a checkpoint."""


@pytest.fixture
def fake():
    with ClassroomFake() as service:
        yield service


@pytest.fixture
def post():
    return ClassroomPost(
        42,
        "course-12",
        "Սեփական ֆունկցիաներ",
        "https://www.youtube.com/watch?v=old_video",
        summary="Հայերեն ամփոփում՝ callable և hash",
        group="Python 12",
        lesson=14,
        date=date(2026, 11, 19),
        duration=123.5,
        instance_id="academy-12345678",
    )


def publisher(fake, kind="announcement", **kwargs):
    settings = ClassroomSettings(mode="live", post_type=kind, **kwargs.pop("settings", {}))
    return ClassroomPublisher(settings, kwargs.pop("tokens", Tokens()), api_url=fake.url, **kwargs)


def seed(fake, post, resource="announcements", *, marker=True):
    text = post.title + ("\n\n" + post.marker() if marker else "")
    body = {"state": "PUBLISHED", "materials": [{"link": {"url": post.url}}]}
    body.update(
        {"text": text}
        if resource == "announcements"
        else {"title": post.title, "description": text}
    )
    return fake.seed(resource, body)


@pytest.mark.parametrize("kind,resource", KINDS)
def test_both_published_bodies_templates_link_cards_and_utf8(fake, post, kind, resource):
    template = (
        "{title}\n{summary}\n{group} · {lesson:02d} · {date:%Y-%m-%d} · {duration:.1f}\n{url}"
    )
    states = []
    result = publisher(fake, kind, settings={"text_template": template}).publish(
        post, None, on_state=states.append
    )
    assert result.created and not result.updated and not result.dry_run
    assert result.post_type == kind and result.url.startswith("https://classroom.google.com/")
    create = next(c for c in fake.calls if c["method"] == "POST")
    body = create["body"]
    assert body["state"] == "PUBLISHED" and body["materials"] == [{"link": {"url": post.url}}]
    field = "text" if kind == "announcement" else "description"
    assert body[field] == render_text(template, post)
    assert post.title.encode("utf-8") in create["raw"] and b"\\u054d" not in create["raw"]
    assert "Python 12 · 14 · 2026-11-19 · 123.5" in body[field]
    if kind == "material":
        assert body["title"] == post.title
    assert result.details == states[-1] and states[-1]["complete"]
    assert result.details["post_id"] == result.post_id and result.details["url"] == result.url
    assert len(fake.active()) == 1
    states[-1]["complete"] = False
    assert result.details["complete"]


@pytest.mark.parametrize("kind,resource", KINDS)
def test_dry_run_needs_no_token_and_never_constructs_http_client(
    fake, post, kind, resource, monkeypatch
):
    def unexpected(*args, **kwargs):
        pytest.fail("dry-run may not construct a live client")

    monkeypatch.setattr("reel.publish.classroom.GoogleApiClient", unexpected)
    result = ClassroomPublisher(ClassroomSettings(post_type=kind), None, api_url=fake.url).publish(
        post, None
    )
    assert result.dry_run and not result.created and not result.updated
    assert result.post_id is None and result.url is None and not fake.calls
    assert (
        result.body["state"] == "PUBLISHED"
        and result.body["materials"][0]["link"]["url"] == post.url
    )
    assert result.details["body"] == result.body


@pytest.mark.parametrize("kind,resource", KINDS)
def test_same_link_rerun_patches_text_title_state_without_materials(fake, post, kind, resource):
    first = publisher(fake, kind).publish(post, None)
    previous = copy.deepcopy(first.details)
    second = publisher(fake, kind).publish(
        replace(post, title="Նոր վերնագիր", summary="Նոր ամփոփում"), previous
    )
    assert second.post_id == first.post_id and second.updated and not second.created
    assert previous == first.details and fake.count("POST") == 1
    patch = next(c for c in fake.calls if c["method"] == "PATCH")
    assert "materials" not in patch["body"] and "materials" not in patch["query"]["updateMask"][0]
    assert patch["body"]["state"] == "PUBLISHED"
    if kind == "material":
        assert patch["body"]["title"] == "Նոր վերնագիր"


@pytest.mark.parametrize("kind,resource", KINDS)
def test_new_video_replaces_post_create_then_checkpoint_then_delete(fake, post, kind, resource):
    first = publisher(fake, kind).publish(post, None)
    observations = []

    def state(details):
        observations.append((details, [item["id"] for item in fake.active()]))

    second = publisher(fake, kind).publish(
        replace(post, url="https://www.youtube.com/watch?v=new_video"),
        first.details,
        on_state=state,
    )
    assert second.created and second.post_id != first.post_id
    assert len(fake.active()) == 1 and fake.active()[0]["id"] == second.post_id
    checkpoint = next(row for row in observations if row[0].get("post_id") == second.post_id)
    assert set(checkpoint[1]) == {first.post_id, second.post_id}
    assert checkpoint[0]["pending_delete"] == [{"post_id": first.post_id, "post_type": kind}]
    writes = [c["method"] for c in fake.calls if c["method"] in {"POST", "DELETE"}]
    assert writes == ["POST", "POST", "DELETE"]
    assert second.details["pending_delete"] == []


@pytest.mark.parametrize("kind,resource", KINDS)
@pytest.mark.parametrize("phase", ["lost_response", "checkpoint"])
def test_crash_after_create_is_recovered_without_a_duplicate(fake, post, kind, resource, phase):
    states = []
    if phase == "lost_response":
        fake.cut_after_create = True
        with pytest.raises(GoogleApiError) as error:
            publisher(fake, kind).publish(post, None)
        assert error.value.retryable
        previous = None
    else:

        def state(details):
            states.append(details)
            if details.get("post_id"):
                raise Crash

        with pytest.raises(Crash):
            publisher(fake, kind).publish(post, None, on_state=state)
        previous = states[-1]
    result = publisher(fake, kind).publish(post, previous)
    assert result.post_id and result.updated and fake.count("POST") == len(fake.active()) == 1


@pytest.mark.parametrize("kind,resource", KINDS)
@pytest.mark.parametrize(
    "phase", ["intent", "lost_create_response", "new_id", "lost_delete_response"]
)
def test_replacement_crashes_resume_creation_and_cleanup(fake, post, kind, resource, phase):
    first = publisher(fake, kind).publish(post, None)
    value = replace(post, url="https://www.youtube.com/watch?v=replaced_video")
    states = []
    fake.cut_after_create = phase == "lost_create_response"
    fake.cut_after_delete = phase == "lost_delete_response"

    def state(details):
        states.append(details)
        if phase == "intent" and details.get("pending_delete") and not details.get("post_id"):
            raise Crash
        if phase == "new_id" and details.get("post_id") not in {None, first.post_id}:
            raise Crash

    with pytest.raises((Crash, GoogleApiError)):
        publisher(fake, kind).publish(value, first.details, on_state=state)
    result = publisher(fake, kind).publish(value, states[-1] if states else first.details)
    assert result.post_id != first.post_id and fake.count("POST") == 2
    assert len(fake.active()) == 1 and fake.active()[0]["id"] == result.post_id
    assert not result.details["pending_delete"]


@pytest.mark.parametrize("kind,resource", KINDS)
def test_recovery_by_marker_with_a_new_url_and_no_saved_state(fake, post, kind, resource):
    old = seed(fake, post, resource)
    result = publisher(fake, kind).publish(
        replace(post, url="https://www.youtube.com/watch?v=changed"), None
    )
    assert result.post_id != old["id"] and fake.posts[(resource, old["id"])]["state"] == "DELETED"
    assert len(fake.active()) == 1


@pytest.mark.parametrize("kind,resource", KINDS)
def test_recovery_by_exact_link_without_marker(fake, post, kind, resource):
    old = seed(fake, post, resource, marker=False)
    result = publisher(fake, kind).publish(post, None)
    assert result.post_id == old["id"] and result.updated and fake.count("POST") == 0
    assert post.marker() in json.dumps(fake.active(), ensure_ascii=False)


@pytest.mark.parametrize("deletion", ["not_found", "deleted_state", "race_404", "race_400"])
def test_teacher_deleted_post_is_recreated_including_patch_races(fake, post, deletion):
    first = publisher(fake).publish(post, None)
    key = ("announcements", first.post_id)
    if deletion == "not_found":
        fake.posts.pop(key)
    elif deletion == "deleted_state":
        fake.posts[key]["state"] = "DELETED"
    else:
        fake.delete_on_patch = int(deletion[-3:])
    result = publisher(fake).publish(post, first.details)
    assert result.created and result.post_id != first.post_id and len(fake.active()) == 1


@pytest.mark.parametrize("status,reason", [(403, "PERMISSION_DENIED"), (404, "NOT_FOUND")])
def test_permission_or_course_not_found_is_permanent_with_teacher_guidance(
    fake, post, status, reason
):
    fake.failures[("GET", "announcements")] = [(status, reason)]
    with pytest.raises(GoogleApiError, match="must be a teacher of that course") as error:
        publisher(fake).publish(post, None)
    assert error.value.status == status and not error.value.retryable
    assert fake.count("POST") == 0


def test_create_permission_error_also_has_teacher_guidance(fake, post):
    fake.failures[("POST", "announcements")] = [(403, "PERMISSION_DENIED")]
    with pytest.raises(GoogleApiError, match="teacher") as error:
        publisher(fake).publish(post, None)
    assert not error.value.retryable and not fake.active()


@pytest.mark.parametrize("count", [1, 2])
def test_401_refreshes_once_and_repeated_401_stops(fake, post, count):
    fake.failures[("GET", "announcements")] = [(401, "UNAUTHENTICATED")] * count
    tokens = Tokens()
    if count == 2:
        with pytest.raises(GoogleApiError) as error:
            publisher(fake, tokens=tokens).publish(post, None)
        assert error.value.status == 401 and not error.value.retryable
    else:
        assert publisher(fake, tokens=tokens).publish(post, None).post_id
    assert tokens.invalidations == 1
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer " + TOKEN
    assert fake.calls[1]["headers"]["Authorization"] == "Bearer " + REFRESHED


def test_tokens_and_authorization_never_appear_in_errors_logs_or_state(fake, post, caplog):
    caplog.set_level(logging.DEBUG)
    states = []
    publisher(fake).publish(post, None, on_state=states.append)
    fake.failures[("GET", "announcements")] = [(403, TOKEN, f"Authorization: Bearer {TOKEN}")]
    with pytest.raises(GoogleApiError) as error:
        publisher(fake).publish(post, None)
    output = str(error.value) + repr(error.value) + caplog.text + json.dumps(states)
    assert TOKEN not in output and REFRESHED not in output and "Authorization" not in output
    assert "Bearer" not in output


@pytest.mark.parametrize("kind,resource", KINDS)
def test_google_upgraded_youtube_card_is_reused(fake, post, kind, resource):
    fake.promote_youtube = True
    first = publisher(fake, kind).publish(post, None)
    second = publisher(fake, kind).publish(post, first.details)
    third = publisher(fake, kind).publish(post, None)
    assert first.post_id == second.post_id == third.post_id
    assert fake.count("POST") == len(fake.active()) == 1


def test_history_pagination_prefers_desired_url_and_cleans_only_own_markers(fake, post):
    fake.page_size = 1
    desired = seed(fake, post)
    own_old = seed(fake, replace(post, url="https://www.youtube.com/watch?v=older"))
    unrelated = seed(fake, replace(post, job_id=99, url="https://www.youtube.com/watch?v=other"))
    result = publisher(fake).publish(post, None)
    assert result.post_id == desired["id"] and fake.count("POST") == 0
    assert fake.posts[("announcements", own_old["id"])]["state"] == "DELETED"
    assert fake.posts[("announcements", unrelated["id"])]["state"] == "PUBLISHED"


def test_history_page_bound_and_repeated_cursor_fail_without_creating(fake, post):
    fake.page_size = 1
    for job in [1, 2, 3]:
        seed(fake, replace(post, job_id=job, url=f"https://example.test/video/{job}"))
    with pytest.raises(GoogleApiError, match="page limit") as error:
        publisher(fake, max_history_pages=1).publish(post, None)
    assert error.value.retryable and fake.count("POST") == 0
    fake.repeat_page_token = True
    with pytest.raises(GoogleApiError) as error:
        publisher(fake).publish(post, None)
    assert error.value.retryable and fake.count("POST") == 0


@pytest.mark.parametrize(
    "response",
    [
        {"announcements": None},
        {"announcements": ["invalid"]},
        {"announcements": [{"materials": None}]},
    ],
)
def test_malformed_history_never_causes_a_blind_create(fake, post, response):
    fake.responses[("GET", "announcements")] = [response]
    with pytest.raises(GoogleApiError) as error:
        publisher(fake).publish(post, None)
    assert error.value.retryable and fake.count("POST") == 0


def test_optional_state_hook_and_missing_created_url_are_supported(fake, post):
    fake.hide_created_url = True
    result = publisher(fake).publish(post, None)
    assert result.url and result.post_id and fake.count("GET") == 2


def test_switching_post_type_creates_new_first_then_deletes_old(fake, post):
    first = publisher(fake).publish(post, None)
    second = publisher(fake, "material").publish(post, first.details)
    assert second.post_type == "material" and second.post_id != first.post_id
    assert len(fake.active()) == 1 and fake.count("DELETE", "announcements") == 1


def test_instance_namespaces_and_previous_identity_validation(fake, post):
    assert post.marker() != replace(post, instance_id="other-instance").marker()
    assert post.marker() == replace(post, url="https://example.test/new").marker()
    wrong = {"post_id": "post_1", "post_type": "announcement", "job_id": 100}
    with pytest.raises(GoogleApiError, match="another job"):
        publisher(fake).publish(post, wrong)
    assert not fake.calls


@pytest.mark.parametrize("template", ["{title.__class__}", "{title[0]}", "{title:{summary}}", " "])
def test_unsafe_or_empty_templates_fail_before_http(fake, post, template):
    with pytest.raises(GoogleApiError, match="template") as error:
        publisher(fake, settings={"text_template": template}).publish(post, None)
    assert not error.value.retryable and not fake.calls


def test_live_tokens_and_course_id_are_required_but_empty_dry_course_is_allowed(fake, post):
    with pytest.raises(GoogleApiError, match="tokens are required") as error:
        ClassroomPublisher(ClassroomSettings(mode="live"), None)
    assert not error.value.retryable
    with pytest.raises(GoogleApiError, match="course id") as error:
        publisher(fake).publish(replace(post, course_id=""), None)
    assert not error.value.retryable
    dry = ClassroomPublisher(ClassroomSettings()).publish(replace(post, course_id=""), None)
    assert dry.dry_run and not fake.calls


def test_check_cancellation_propagates_and_stops_before_create(fake, post):
    def check():
        if fake.calls:
            raise Crash("cancelled")

    with pytest.raises(Crash, match="cancelled"):
        publisher(fake).publish(post, None, check=check)
    assert fake.count("POST") == 0


@pytest.mark.parametrize(
    "status,reason", [(503, "backendError"), (403, "quotaExceeded"), (429, "RESOURCE_EXHAUSTED")]
)
def test_transient_history_errors_propagate_without_creating(fake, post, status, reason):
    fake.failures[("GET", "announcements")] = [(status, reason)]
    with pytest.raises(GoogleApiError) as error:
        publisher(fake).publish(post, None)
    assert error.value.retryable and fake.count("POST") == 0
    if reason == "quotaExceeded":
        assert "daily quota" in str(error.value)


@pytest.mark.parametrize("kind,resource", KINDS)
def test_minimal_previous_id_and_type_are_enough_to_update(fake, post, kind, resource):
    item = seed(fake, post, resource)
    result = publisher(fake, kind).publish(post, {"post_id": item["id"], "post_type": kind})
    assert result.post_id == item["id"] and result.updated and fake.count("POST") == 0


def test_course_not_found_on_create_is_permanent(fake, post):
    fake.failures[("POST", "announcements")] = [(404, "NOT_FOUND")]
    with pytest.raises(GoogleApiError, match="teacher of that course") as error:
        publisher(fake).publish(post, None)
    assert not error.value.retryable and not fake.active()


def test_missing_alternate_link_is_not_reported_as_success_and_id_is_saved(fake, post):
    item = seed(fake, post)
    item["alternateLink"] = "https://untrusted.example/not-classroom"
    fake.responses[("POST", "announcements")] = [item]
    fake.responses[("GET", "announcements")] = [{}]  # Initial empty history.
    states = []
    with pytest.raises(GoogleApiError) as error:
        publisher(fake).publish(post, None, on_state=states.append)
    assert error.value.retryable and states[-1]["post_id"] == item["id"]
    assert not states[-1]["complete"] and "untrusted.example" not in str(error.value)


@pytest.mark.parametrize("kind,resource", KINDS)
def test_server_returning_a_draft_cannot_produce_fake_published_success(fake, post, kind, resource):
    item = seed(fake, post, resource)
    item["state"] = "DRAFT"
    fake.responses[("GET", resource)] = [{}]
    fake.responses[("POST", resource)] = [item]
    states = []
    with pytest.raises(GoogleApiError) as error:
        publisher(fake, kind).publish(post, None, on_state=states.append)
    assert error.value.retryable and states[-1]["post_id"] == item["id"]
    result = publisher(fake, kind).publish(post, states[-1])
    assert result.post_id == item["id"] and result.updated and item["state"] == "PUBLISHED"


@pytest.mark.parametrize("title", ["", "ա" * 3001])
def test_invalid_material_title_fails_before_http(fake, post, title):
    with pytest.raises(GoogleApiError) as error:
        publisher(fake, "material", settings={"text_template": "{summary}"}).publish(
            replace(post, title=title),
            None,
        )
    assert not error.value.retryable and not fake.calls


def test_text_limit_includes_marker_and_supports_armenian_characters(post):
    room = 30_000 - 2 - len(post.marker())
    full = render_text("{summary}", replace(post, summary="ա" * room))
    assert len(full) == 30_000 and len(full.encode("utf-8")) > 30_000
    with pytest.raises(GoogleApiError, match="30000"):
        render_text("{summary}", replace(post, summary="ա" * (room + 1)))


@pytest.mark.parametrize("course", ["..", ".", "bad/course", "bad?course"])
def test_invalid_course_path_is_rejected_without_http(fake, post, course):
    with pytest.raises(GoogleApiError):
        publisher(fake).publish(replace(post, course_id=course), None)
    assert not fake.calls


def test_course_alias_is_encoded_as_one_path_component(fake, post):
    result = publisher(fake).publish(replace(post, course_id="d:python.class"), None)
    assert result.post_id and all(call["course"] == "d:python.class" for call in fake.calls)
