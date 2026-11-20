"""Deterministic metadata, chapter rules, and UTF-8 description limits (T-502)."""

import math
from datetime import date

import pytest

from reel.config.schema import MetadataSettings
from reel.metadata import (
    Chapter,
    ChapterError,
    DescriptionResult,
    MetadataTextError,
    build_description,
    check_chapters,
    format_timestamp,
    render_title,
)


@pytest.fixture
def settings():
    return MetadataSettings()


@pytest.fixture
def chapters():
    return [Chapter(0, "Ներածություն"), Chapter(10, "Ֆունկցիաներ"), Chapter(20, "Օրինակներ")]


@pytest.mark.parametrize(
    "seconds,short,long",
    [
        (0, "0:00", "0:00:00"),
        (0.99, "0:00", "0:00:00"),
        (59.99, "0:59", "0:00:59"),
        (545, "9:05", "0:09:05"),
        (3599.9, "59:59", "0:59:59"),
        (3600, "60:00", "1:00:00"),
        (3723.9, "62:03", "1:02:03"),
        (360_001, "6000:01", "100:00:01"),
    ],
)
def test_timestamp_floors_seconds_and_obeys_the_video_format(seconds, short, long):
    assert format_timestamp(seconds, long=False) == short
    assert format_timestamp(seconds, long=True) == long


@pytest.mark.parametrize("value", [-0.1, math.nan, math.inf, -math.inf, True, "10", None])
def test_timestamp_rejects_invalid_numbers(value):
    with pytest.raises(MetadataTextError, match="finite nonnegative"):
        format_timestamp(value, long=False)


def test_timestamp_requires_boolean_format_choice():
    with pytest.raises(MetadataTextError, match="boolean"):
        format_timestamp(10, long=1)


@pytest.mark.parametrize("first", [0, 0.9, 9.999, 10])
def test_early_first_chapter_moves_to_zero_without_mutating_input(settings, first):
    items = [
        Chapter(first, "  Հայերեն\n ներածություն  "),
        Chapter(20, "A < B > C"),
        Chapter(30, "End"),
    ]
    before = list(items)
    result = check_chapters(iter(items), 40, settings)
    assert result == [
        Chapter(0, "Հայերեն ներածություն"),
        Chapter(20, "A ( B ) C"),
        Chapter(30, "End"),
    ]
    assert items == before and result is not items


@pytest.mark.parametrize(
    "starts,duration,message",
    [
        ([], 30, "At least 3"),
        ([0, 10], 30, "At least 3"),
        ([-1, 10, 20], 30, "nonnegative"),
        ([math.nan, 10, 20], 30, "finite"),
        ([0, math.inf, 20], 30, "finite"),
        ([0, True, 20], 30, "finite"),
        ([0, 10, 10], 30, "ascending"),
        ([0, 20, 10], 30, "ascending"),
        ([10, 10, 20], 30, "ascending"),
        ([10.01, 20, 30], 40, "first chapter"),
        ([0, 10, 30], 30, "outside"),
        ([0, 10, 31], 30, "outside"),
        ([0, 9.999, 20], 30, "Chapter 1 must last"),
        ([0, 10, 19.999], 30, "Chapter 2 must last"),
        ([0, 10, 20], 29.999, "Chapter 3 must last"),
    ],
)
def test_chapter_rule_errors_have_clear_reasons(settings, starts, duration, message):
    items = [Chapter(start, "Title") for start in starts]
    with pytest.raises(ChapterError, match=message):
        check_chapters(items, duration, settings)


@pytest.mark.parametrize("duration", [0, -1, math.nan, math.inf, True, "30"])
def test_chapters_require_a_positive_finite_duration(settings, chapters, duration):
    with pytest.raises(ChapterError, match="Video duration"):
        check_chapters(chapters, duration, settings)


@pytest.mark.parametrize("title", ["", " \n\t ", None])
def test_empty_or_invalid_chapter_titles_are_refused(settings, title):
    items = [Chapter(0, "Start"), Chapter(10, title), Chapter(20, "End")]
    with pytest.raises(ChapterError):
        check_chapters(items, 30, settings)


def test_chapter_input_must_use_the_public_chapter_type(settings):
    with pytest.raises(ChapterError, match="Chapter value"):
        check_chapters([{"start": 0, "title": "bad"}] * 3, 30, settings)


def test_custom_count_lengths_and_chapter_title_limit_are_honoured():
    settings = MetadataSettings(min_chapters=4, min_chapter_seconds=15, max_chapter_title_chars=10)
    items = [Chapter(i * 15, "abc def ghi jkl") for i in range(4)]
    result = check_chapters(items, 60, settings)
    assert [item.title for item in result] == ["abc def…"] * 4
    with pytest.raises(ChapterError, match="At least 4"):
        check_chapters(items[:3], 60, settings)
    with pytest.raises(ChapterError, match="Chapter 4 must last at least 15"):
        check_chapters(items, 59.99, settings)


def test_float_boundary_roundoff_does_not_reject_an_exact_ten_second_chapter(settings):
    items = [Chapter(0, "A"), Chapter(10.9, "B"), Chapter(20.9, "C")]
    assert len(check_chapters(items, 30.9, settings)) == 3


def test_long_single_word_chapter_title_and_unicode_title_fit_limits(settings):
    items = [Chapter(0, "ա" * 200), Chapter(10, "Հայերեն բառեր " * 20), Chapter(20, "End")]
    result = check_chapters(items, 30, settings)
    assert result[0].title == "ա" * 59 + "…"
    assert len(result[0].title) == 60 and len(result[0].title.encode("utf-8")) > 60
    assert result[1].title.endswith("…") and len(result[1].title) <= 60
    assert not result[1].title.endswith(" …")


def test_default_english_title_and_topic_only_when_template_requests_it(settings):
    assert (
        render_title(settings, group="Python 12", lesson=14, date=None, topic="Ignored")
        == "Python 12 · Lecture 14"
    )


def test_armenian_topic_template_and_date_format():
    # T-502 permits the renderer; Claude must add topic to the shared TITLE_FIELDS whitelist.
    settings = MetadataSettings.model_construct(
        title_template="{group} · Դաս {lesson:02d} · {date:%Y-%m-%d}: {topic}"
    )
    title = render_title(
        settings, group="Python 12", lesson=14, date=date(2026, 11, 19), topic="Բառարաններ"
    )
    assert title == "Python 12 · Դաս 14 · 2026-11-19: Բառարաններ"
    without_topic = render_title(settings, group="Python 12", lesson=14, date=None)
    assert "None" not in without_topic and "  " not in without_topic


def test_missing_optional_title_fields_even_with_format_specs_become_empty():
    settings = MetadataSettings(title_template="{group} {lesson:02d} {date:%Y-%m-%d}")
    assert render_title(settings, group="Հայերեն", lesson=None, date=None) == "Հայերեն"


def test_title_truncation_uses_characters_and_word_boundaries():
    settings = MetadataSettings(title_template="{group}")
    title = render_title(settings, group="Հայերեն " * 40, lesson=None, date=None)
    assert len(title) <= 100 and title.endswith("…")
    assert title.removesuffix("…").split() == ["Հայերեն"] * len(title.removesuffix("…").split())
    assert len(title.encode("utf-8")) > 100
    assert render_title(settings, group="ա" * 101, lesson=None, date=None) == "ա" * 99 + "…"
    assert render_title(settings, group="ա" * 100, lesson=None, date=None) == "ա" * 100


def test_title_replaces_angles_and_collapses_all_whitespace():
    settings = MetadataSettings(title_template="  {group}\n\t {lesson}  ")
    assert render_title(settings, group="A < B > C", lesson=14, date=None) == "A ( B ) C 14"


@pytest.mark.parametrize(
    "template", ["{group.__class__}", "{group[0]}", "{group:{lesson}}", "{missing}"]
)
def test_title_rejects_unsafe_or_unknown_template_fields(template):
    settings = MetadataSettings.model_construct(title_template=template)
    with pytest.raises(MetadataTextError, match="template"):
        render_title(settings, group="A", lesson=14, date=None)


def test_empty_rendered_title_is_a_clear_error():
    with pytest.raises(MetadataTextError, match="must not be empty"):
        render_title(
            MetadataSettings(title_template="{group}"), group=" \n ", lesson=None, date=None
        )


def test_description_joins_summary_chapters_and_rendered_multiline_footer(settings, chapters):
    settings = MetadataSettings(description_footer="{group} · {lesson}\n{date:%Y-%m-%d}\n{title}")
    result = build_description(
        summary="Հայերեն\n ամփոփում",
        chapters=chapters,
        duration=30,
        footer_fields={
            "group": "Python 12",
            "lesson": 14,
            "date": date(2026, 11, 19),
            "title": "Դաս",
        },
        settings=settings,
    )
    assert isinstance(result, DescriptionResult) and result.flags == ()
    assert result.text == (
        "Հայերեն ամփոփում\n\n0:00 Ներածություն\n0:10 Ֆունկցիաներ\n0:20 Օրինակներ"
        "\n\nPython 12 · 14\n2026-11-19\nԴաս"
    )


@pytest.mark.parametrize(
    "duration,expected",
    [
        (3599.9, "0:00 A\n0:10 B\n0:20 C"),
        (3600, "0:00:00 A\n0:00:10 B\n0:00:20 C"),
        (3723.9, "0:00:00 A\n0:00:10 B\n0:00:20 C"),
    ],
)
def test_description_uses_one_timestamp_format_for_the_entire_video(settings, duration, expected):
    result = build_description(
        summary="",
        chapters=[Chapter(0, "A"), Chapter(10, "B"), Chapter(20, "C")],
        duration=duration,
        footer_fields={},
        settings=settings,
    )
    assert result.text == expected


def test_description_omits_empty_sections_and_missing_footer_fields(settings):
    settings = MetadataSettings(description_footer="{group} {date:%Y-%m-%d} {lesson:02d}")
    result = build_description(
        summary="Հայերեն", chapters=[], duration=30, footer_fields={}, settings=settings
    )
    assert result == DescriptionResult("Հայերեն")
    assert (
        build_description(
            summary="", chapters=[], duration=30, footer_fields={}, settings=settings
        ).text
        == ""
    )


def test_description_replaces_angles_in_all_three_sections(chapters):
    settings = MetadataSettings(description_footer="<Դաս> {title}")
    items = [Chapter(c.start, "<" + c.title + ">") for c in chapters]
    result = build_description(
        summary="A < B > C",
        chapters=items,
        duration=30,
        footer_fields={"title": "<Title>"},
        settings=settings,
    )
    assert "<" not in result.text and ">" not in result.text
    assert (
        "A ( B ) C" in result.text
        and "0:00 (Ներածություն)" in result.text
        and "(Դաս) (Title)" in result.text
    )


def test_armenian_description_byte_truncation_keeps_chapters_and_footer_intact(settings, chapters):
    settings = MetadataSettings(description_footer="Footer: {group}")
    result = build_description(
        summary="Հայերեն բառեր " * 1000,
        chapters=chapters,
        duration=30,
        footer_fields={"group": "Դասընթաց"},
        settings=settings,
    )
    expected = "0:00 Ներածություն\n0:10 Ֆունկցիաներ\n0:20 Օրինակներ\n\nFooter: Դասընթաց"
    assert result.text.endswith(expected) and len(result.text.encode("utf-8")) <= 5000
    assert result.text.split("\n\n")[0].endswith("…")
    assert result.flags == ("description_summary_truncated",)
    assert result.text.encode("utf-8").decode("utf-8") == result.text


def test_exact_description_byte_limit_is_not_truncated(settings):
    summary = "ա" * 2500
    result = build_description(
        summary=summary, chapters=[], duration=30, footer_fields={}, settings=settings
    )
    assert result == DescriptionResult(summary)
    overflow = build_description(
        summary=summary + "բ", chapters=[], duration=30, footer_fields={}, settings=settings
    )
    assert len(overflow.text.encode("utf-8")) <= 5000 and overflow.text.endswith("…")
    assert overflow.flags == ("description_summary_truncated",)


def test_chapter_block_that_alone_exceeds_limit_is_dropped_as_a_whole():
    settings = MetadataSettings(max_chapter_title_chars=100, description_footer="Footer")
    chapters = [Chapter(index * 10, "ա" * 100) for index in range(30)]
    result = build_description(
        summary="Ամփոփում", chapters=chapters, duration=300, footer_fields={}, settings=settings
    )
    assert result.text == "Ամփոփում\n\nFooter"
    assert result.flags == ("description_chapters_dropped",)


def test_huge_footer_is_shortened_after_summary_without_breaking_chapters(chapters):
    settings = MetadataSettings(description_footer="{title}")
    result = build_description(
        summary="Ամփոփում",
        chapters=chapters,
        duration=30,
        footer_fields={"title": "ա" * 4000},
        settings=settings,
    )
    assert result.text.startswith("0:00 Ներածություն\n0:10 Ֆունկցիաներ\n0:20 Օրինակներ\n\n")
    assert "Ամփոփում" not in result.text and result.text.endswith("…")
    assert set(result.flags) == {"description_footer_truncated", "description_summary_truncated"}
    assert len(result.text.encode("utf-8")) <= 5000


def test_invalid_nonempty_chapter_list_is_not_silently_discarded(settings):
    with pytest.raises(ChapterError, match="At least 3"):
        build_description(
            summary="A",
            chapters=[Chapter(0, "A")],
            duration=30,
            footer_fields={},
            settings=settings,
        )


def test_footer_template_uses_only_approved_fields(settings):
    bad = MetadataSettings.model_construct(description_footer="{title.__class__}")
    with pytest.raises(MetadataTextError, match="template"):
        build_description(
            summary="A", chapters=[], duration=30, footer_fields={"title": "x"}, settings=bad
        )


def test_literal_braces_and_missing_optional_footer_values_are_supported():
    settings = MetadataSettings(
        title_template="{{{group}}}", description_footer="{{Academy}} {date:%Y-%m-%d}"
    )
    assert render_title(settings, group="Հայերեն", lesson=None, date=None) == "{Հայերեն}"
    result = build_description(
        summary="A", chapters=[], duration=30, footer_fields={}, settings=settings
    )
    assert result.text == "A\n\n{Academy}"


@pytest.mark.parametrize("bad", [None, 42])
def test_noniterable_chapter_inputs_raise_the_shared_chapter_error(settings, bad):
    with pytest.raises(ChapterError, match="iterable"):
        check_chapters(bad, 30, settings)
    with pytest.raises(ChapterError, match="iterable"):
        build_description(
            summary="A", chapters=bad, duration=30, footer_fields={}, settings=settings
        )


@pytest.mark.parametrize("extra,dropped", [(0, False), (1, True)])
def test_exact_5000_byte_chapter_block_is_intact_and_5001_is_dropped(extra, dropped):
    settings = MetadataSettings(max_chapter_title_chars=100, description_footer="Footer")
    items = [Chapter(index * 10, "A" * (95 + extra if index == 0 else 94)) for index in range(50)]
    block = "\n".join(
        f"{format_timestamp(chapter.start, long=False)} {chapter.title}" for chapter in items
    )
    assert len(block.encode("utf-8")) == 5000 + extra
    result = build_description(
        summary="Summary", chapters=items, duration=500, footer_fields={}, settings=settings
    )
    assert len(result.text.encode("utf-8")) <= 5000
    if dropped:
        assert result == DescriptionResult("Summary\n\nFooter", ("description_chapters_dropped",))
    else:
        assert result.text == block
        assert set(result.flags) == {
            "description_summary_truncated",
            "description_footer_truncated",
        }


def test_fractional_starts_are_retained_except_for_the_first(settings):
    items = [Chapter(0.5, "A"), Chapter(10.9, "B"), Chapter(20.9, "C")]
    result = check_chapters(items, 30.9, settings)
    assert [chapter.start for chapter in result] == [0, 10.9, 20.9]
    description = build_description(
        summary="", chapters=result, duration=30.9, footer_fields={}, settings=settings
    )
    assert description.text == "0:00 A\n0:10 B\n0:20 C"


def test_float_tolerance_cannot_hide_a_published_nine_second_chapter(settings):
    items = [Chapter(0, "A"), Chapter(10 - 1e-10, "B"), Chapter(20, "C")]
    with pytest.raises(ChapterError, match="timestamp flooring"):
        check_chapters(items, 30, settings)


def test_fractional_minimum_must_also_hold_for_published_timestamps():
    settings = MetadataSettings(min_chapter_seconds=10.5)
    items = [Chapter(0, "A"), Chapter(10.5, "B"), Chapter(21, "C")]
    with pytest.raises(ChapterError, match="timestamp flooring"):
        check_chapters(items, 31.5, settings)
    valid = [Chapter(0, "A"), Chapter(11.7, "B"), Chapter(22.6, "C")]
    assert check_chapters(valid, 33.1, settings) == valid
