"""A video whose speech is not clear: staff publish it as it is, with their chapters, or
cancel it (T-1012, D-064)."""

import json

import pytest
from test_web_dashboard import add_job
from test_web_detail import events, job, post, site  # noqa: F401  (site is a fixture)

from reel.config.schema import MetadataSettings
from reel.db import Job, JobStatus, Stage, VideoMetadata
from reel.metadata.chapters import NOT_GENERATED
from reel.stages.chapters import (
    METADATA_FILE,
    SPEECH_UNCLEAR,
    STAFF_CHAPTERS,
    unclear_speech_message,
)
from reel.web import texts
from reel.web.forms import parse_chapters

RULES = MetadataSettings()
CHAPTERS = "0:00 Ներածություն\n\n4:30 - Ֆունկցիաներ\n(12:05) Օրինակներ\n"


# --- the list staff type ------------------------------------------------------------------


def test_chapters_are_read_one_per_line():
    found = parse_chapters(CHAPTERS, 900.0, RULES)
    assert [(c.start, c.title) for c in found] == [
        (0.0, "Ներածություն"),
        (270.0, "Ֆունկցիաներ"),
        (725.0, "Օրինակներ"),
    ]
    long = parse_chapters("0:05 Ա\n59:00 Բ\n1:02:05 Գ", 4000.0, RULES)
    assert [c.start for c in long] == [0.0, 3540.0, 3725.0]  # a start within 10 s moves to 0


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("0:00 Ա\nԲ առանց ժամի\n9:00 Գ", "Տող 2"),
        ("0:00 Ա\n4:75 Բ\n9:00 Գ", "Տող 2"),
        ("0:00 Ա\n5:00\n9:00 Գ", "Տող 2"),  # no title
        ("0:00 Ա\n5:00 Բ", "առնվազն 3 բաժին"),
        ("", "առնվազն 3 բաժին"),
        ("0:30 Ա\n5:00 Բ\n9:00 Գ", "0:00-ից"),
        ("0:00 Ա\n9:00 Բ\n5:00 Գ", "հերթականությամբ"),
        ("0:00 Ա\n5:00 Բ\n16:00 Գ", "տեսանյութից դուրս"),
        ("0:00 Ա\n5:00 Բ\n5:05 Գ", "առնվազն 10 վայրկյան"),
        ("0:00 Ա\n5:00 Բ\n14:55 Գ", "առնվազն 10 վայրկյան"),  # the last one ends with the video
    ],
)
def test_mistakes_are_explained_in_armenian(text, words):
    with pytest.raises(ValueError, match=words):
        parse_chapters(text, 900.0, RULES)


# --- the dashboard ------------------------------------------------------------------------


def unclear_hold(site, *, folder: bool = True) -> int:  # noqa: F811
    job_id = add_job(site.db, group="Python 12", lesson=3, status=JobStatus.ON_HOLD,
                     stage=Stage.CHAPTERS, hold_reason=SPEECH_UNCLEAR,
                     hold_message=unclear_speech_message(0, 900.0))  # fmt: skip
    with site.db.transaction() as s:
        s.get(Job, job_id).flags = [NOT_GENERATED, SPEECH_UNCLEAR]
        s.add(VideoMetadata(job_id=job_id, title="Python 12 · Lecture 3", summary="",
                            description="", chapters=[],
                            generator={"chapter_details": {"duration": 900.0}}))  # fmt: skip
    if folder:
        (site.cfg.paths.data_dir / "jobs" / str(job_id)).mkdir(parents=True)
    return job_id


def metadata(db, job_id):
    with db.transaction() as s:
        row = s.get(Job, job_id).video_metadata
        return row.chapters, row.description, dict(row.generator)


def test_staff_see_why_and_the_two_ways_to_publish(site):  # noqa: F811
    job_id = unclear_hold(site)
    html = site.client.get(f"/recordings/{job_id}").text
    assert texts.HOLD_LABELS[SPEECH_UNCLEAR] in html
    assert "խոսքը պարզ չէ (0 ճանաչված բառ 15 րոպեում)" in html
    assert "Տեղադրել առանց բաժինների" in html and "Պահպանել բաժինները և տեղադրել" in html
    assert "Չեղարկել job-ը" in html
    assert "Կրկին ստուգել" not in html  # running the stage again finds the same


def test_staff_publish_the_video_without_chapters(site):  # noqa: F811
    job_id = unclear_hold(site)
    response = post(site, job_id, "publish", note="Ձայնը չկա, բայց գրատախտակը պարզ է")
    assert response.status_code == 303
    assert response.headers["location"] == f"/recordings/{job_id}?done=published"
    assert texts.DONE_MESSAGES["published"] in site.client.get(response.headers["location"]).text
    state = job(site.db, job_id)
    assert (state.status, state.stage) == (JobStatus.QUEUED, Stage.CONSENT)
    assert events(site.db, job_id)[-1] == (
        "released",
        "anna",
        "published without chapters: Ձայնը չկա, բայց գրատախտակը պարզ է",
    )
    assert metadata(site.db, job_id)[0] == []


def test_staff_set_the_chapters_and_the_video_goes_on_with_them(site):  # noqa: F811
    job_id = unclear_hold(site)
    response = post(site, job_id, "chapters", chapters=CHAPTERS)
    assert response.status_code == 303
    assert response.headers["location"] == f"/recordings/{job_id}?done=chapters"
    state = job(site.db, job_id)
    assert (state.status, state.stage) == (JobStatus.QUEUED, Stage.CONSENT)
    assert STAFF_CHAPTERS in state.flags and NOT_GENERATED not in state.flags
    chapters, description, generator = metadata(site.db, job_id)
    assert chapters == [
        {"start": 0.0, "title": "Ներածություն"},
        {"start": 270.0, "title": "Ֆունկցիաներ"},
        {"start": 725.0, "title": "Օրինակներ"},
    ]
    assert "0:00 Ներածություն\n4:30 Ֆունկցիաներ\n12:05 Օրինակներ" in description
    assert generator["staff_chapters"]["by"] == "anna"
    assert generator["chapter_details"] == {"duration": 900.0}
    saved = json.loads(
        (site.cfg.paths.data_dir / "jobs" / str(job_id) / METADATA_FILE).read_text("utf-8")
    )
    assert saved["chapters"] == chapters and saved["description"] == description
    assert events(site.db, job_id)[-1] == (
        "released",
        "anna",
        "published with 3 chapters set by staff",
    )


def test_a_wrong_list_is_shown_again_with_the_reason(site):  # noqa: F811
    job_id = unclear_hold(site)
    typed = "0:00 Ներածություն\n9:00 Ֆունկցիաներ\n5:00 Օրինակներ"
    response = post(site, job_id, "chapters", chapters=typed)
    assert response.status_code == 422
    assert "հերթականությամբ" in response.text
    assert "9:00 Ֆունկցիաներ" in response.text  # the typed text is kept in the field
    state = job(site.db, job_id)
    assert (state.status, state.stage) == (JobStatus.ON_HOLD, Stage.CHAPTERS)
    assert metadata(site.db, job_id)[0] == []


@pytest.mark.parametrize("action", ["publish", "chapters"])
def test_only_a_job_waiting_for_this_decision_can_be_published_so(site, action):  # noqa: F811
    job_id = add_job(site.db, group="Python 12", lesson=3, status=JobStatus.ON_HOLD,
                     stage=Stage.CONSENT, hold_reason="consent_opted_out")  # fmt: skip
    response = post(site, job_id, action, chapters=CHAPTERS)
    assert response.status_code == 409
    state = job(site.db, job_id)
    assert (state.status, state.stage) == (JobStatus.ON_HOLD, Stage.CONSENT)


def test_staff_can_still_cancel_it(site):  # noqa: F811
    job_id = unclear_hold(site, folder=False)
    assert post(site, job_id, "cancel", note="Ձայն չկա").status_code == 303
    assert job(site.db, job_id).status is JobStatus.CANCELLED
