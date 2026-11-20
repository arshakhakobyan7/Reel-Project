"""Group, lesson number and date identification (T-203), table-driven."""

import os
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from reel.config import load_config
from reel.config.groups import GroupsFile
from reel.config.schema import IngestSettings
from reel.db import Recording
from reel.ingest.identify import (
    AMBIGUOUS_LESSON,
    MISSING_DATE,
    MISSING_LESSON,
    UNKNOWN_GROUP,
    identify,
    identify_recording,
)
from reel.queue.jobs import create_job

WATCH = Path(os.path.abspath(os.sep + "inbox"))
GROUPS = GroupsFile.model_validate(
    {"groups": {"Python 12": {"aliases": ["Py12"]}, "Վեբ 5": {"aliases": ["web-5"]}}}
)
YEREVAN = ZoneInfo("Asia/Yerevan")
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
MEDIA = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)  # 19:00 in Yerevan → 18 Nov
FILE = datetime(2026, 11, 19, 21, 30, tzinfo=UTC)  # 01:30 in Yerevan → 20 Nov


def run(name, *, media=MEDIA, file=FILE, ingest=None, watch=(WATCH,), root=WATCH):
    return identify(
        root / name,
        watch_folders=watch,
        groups=GROUPS,
        ingest=ingest or IngestSettings(),
        timezone=YEREVAN,
        creation_time=media,
        file_modified_at=file,
        now=NOW,
    )


@pytest.mark.parametrize(
    ("name", "media", "group", "lesson", "day", "source", "flags"),
    [
        ("Python 12/Lecture 14 2026-11-19.mp4", MEDIA, "Python 12", 14, 19, "file name", ()),
        ("py12/L7.mkv", MEDIA, "Python 12", 7, 18, "media creation time", ()),
        ("PYTHON_12/Դաս 3 19.11.2026.mp4", None, "Python 12", 3, 19, "file name", ()),
        ("WEB-5/ԴԱՍ 21.mp4", None, "Վեբ 5", 21, 20, "file time", ()),
        ("Վեբ 5/lesson_08.mp4", MEDIA, "Վեբ 5", 8, 18, "media creation time", ()),
        ("Python 12/2026-11/Lesson 5.mp4", MEDIA, "Python 12", 5, 18, "media creation time", ()),
        ("Python 12/Lecture 14 L14.mp4", MEDIA, "Python 12", 14, 18, "media creation time", ()),
        ("Python 12/Lec 2 2026-02-30.mp4", MEDIA, "Python 12", 2, 18, "media creation time", ()),
        (
            "Unknown group/Lecture 2.mp4",
            MEDIA,
            None,
            2,
            18,
            "media creation time",
            (UNKNOWN_GROUP,),
        ),
        ("Lecture 2.mp4", MEDIA, None, 2, 18, "media creation time", (UNKNOWN_GROUP,)),
        (
            "Python 12/recording.mp4",
            MEDIA,
            "Python 12",
            None,
            18,
            "media creation time",
            (MISSING_LESSON,),
        ),
        (
            "Python 12/HTML5 basics.mp4",
            MEDIA,
            "Python 12",
            None,
            18,
            "media creation time",
            (MISSING_LESSON,),
        ),
        (
            "Python 12/Lecture 3 L4.mp4",
            MEDIA,
            "Python 12",
            None,
            18,
            "media creation time",
            (AMBIGUOUS_LESSON,),
        ),
        (
            "Other/Lecture 99999.mp4",
            None,
            None,
            None,
            20,
            "file time",
            (UNKNOWN_GROUP, MISSING_LESSON),
        ),
    ],
)
def test_names(name, media, group, lesson, day, source, flags):
    result = run(name, media=media)
    assert (result.group, result.lesson_number) == (group, lesson)
    assert result.recording_date == date(2026, 11, day)
    assert result.details["date_source"] == source
    assert result.flags == flags
    assert result.complete is (not flags)


@pytest.mark.parametrize(
    "media",
    [
        datetime(1970, 1, 1, tzinfo=UTC),  # encoder default, not a date
        datetime(1904, 1, 1, tzinfo=UTC),
        datetime(2026, 11, 25, tzinfo=UTC),  # in the future
    ],
)
def test_implausible_media_times_are_ignored(media):
    result = run("Python 12/Lecture 1.mp4", media=media)
    assert result.recording_date == date(2026, 11, 20)
    assert result.details["date_source"] == "file time"


def test_a_naive_media_time_counts_as_utc():
    result = run("Python 12/Lecture 1.mp4", media=datetime(2026, 11, 18, 21, 0))
    assert result.recording_date == date(2026, 11, 19)  # 01:00 in Yerevan


def test_no_date_at_all_is_a_flag():
    result = run("Python 12/Lecture 1.mp4", media=None, file=None)
    assert (result.recording_date, result.flags) == (None, (MISSING_DATE,))


def test_patterns_come_from_config():
    ingest = IngestSettings(
        lesson_number_patterns=(r"(?i)week[\s_]*(\d+)",),
        date_patterns=(r"(?P<year>20\d{2})(?P<month>\d{2})(?P<day>\d{2})",),
    )
    result = run("Python 12/Week 3 20261101.mp4", ingest=ingest)
    assert (result.lesson_number, result.recording_date) == (3, date(2026, 11, 1))
    assert run("Python 12/Lecture 3.mp4", ingest=ingest).flags == (MISSING_LESSON,)


def test_files_outside_the_watch_folders_use_their_parent_folder():
    elsewhere = Path(os.path.abspath(os.sep + "imported"))
    assert run("Py12/Lecture 1.mp4", root=elsewhere).group == "Python 12"


def test_nested_watch_folders_use_the_outermost_one():
    result = run("Python 12/Lecture 1.mp4", watch=(WATCH / "Python 12", WATCH))
    assert result.group == "Python 12"


def test_apply_stores_values_and_flags(db):
    with db.transaction() as s:
        recording = Recording(source_path=str(WATCH / "x" / "a.mp4"), size_bytes=1)
        s.add(recording)
        job = create_job(s, recording)
        result = run("Unknown/recording.mp4")
        result.apply(recording, job)
        result.apply(recording, job)  # a re-run does not duplicate flags
    with db.transaction() as s:
        recording = s.get(Recording, recording.id)
        assert recording.group_name is None and recording.lesson_number is None
        assert recording.recording_date == date(2026, 11, 18)
        assert recording.identification["flags"] == [UNKNOWN_GROUP, MISSING_LESSON]
        assert recording.identification["folders"] == ["Unknown"]
        assert recording.job.flags == [UNKNOWN_GROUP, MISSING_LESSON]


def test_identify_recording_uses_the_loaded_config(project):
    cfg = load_config(project / "config" / "reel.toml")
    recording = Recording(
        source_path=str(project / "inbox" / "Py12" / "Դաս 4.mp4"),
        size_bytes=1,
        file_modified_at=FILE,
    )
    result = identify_recording(cfg, recording)
    assert (result.group, result.lesson_number, result.recording_date) == (
        "Python 12",
        4,
        date(2026, 11, 20),
    )
