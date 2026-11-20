"""Deterministic chapter validation and YouTube-ready metadata text (T-502)."""

import math
import string
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date as Date
from typing import Any

from reel.config.schema import FOOTER_FIELDS, TITLE_FIELDS, MetadataSettings, check_template

TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 5000
ELLIPSIS = "…"


class MetadataTextError(ValueError):
    """Metadata inputs or a template cannot produce valid publication text."""


class ChapterError(MetadataTextError):
    """The chapter sequence cannot satisfy the configured YouTube rules."""


@dataclass(frozen=True)
class Chapter:
    start: float
    title: str


@dataclass(frozen=True)
class DescriptionResult:
    text: str
    flags: tuple[str, ...] = ()


def _seconds(value: float, label: str, *, positive: bool = False) -> float:
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("not a number")
        result = float(value)
        if not math.isfinite(result) or result < 0 or (positive and result == 0):
            raise ValueError("invalid number")
        return result
    except ValueError, OverflowError:
        bound = "positive" if positive else "nonnegative"
        raise MetadataTextError(f"{label} must be a finite {bound} number.") from None


def format_timestamp(seconds: float, *, long: bool) -> str:
    """Floor seconds and use either M:SS or H:MM:SS consistently for a video."""
    if type(long) is not bool:
        raise MetadataTextError("long must be a boolean.")
    whole = math.floor(_seconds(seconds, "Timestamp"))
    minutes, seconds = divmod(whole, 60)
    if long:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _clean(text: str) -> str:
    if not isinstance(text, str):
        raise MetadataTextError("Metadata text must be a string.")
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise MetadataTextError("Metadata text must be valid Unicode.") from None
    return text.replace("<", "(").replace(">", ")")


def _line(text: str) -> str:
    return " ".join(_clean(text).split())


def _ellipsis(text: str, prefix: str) -> str:
    if prefix and not prefix[-1].isspace() and not text[len(prefix)].isspace():
        parts = prefix.rsplit(None, 1)
        if len(parts) == 2:
            prefix = parts[0]
    return prefix.rstrip() + ELLIPSIS


def _shorten_chars(text: str, limit: int) -> str:
    return text if len(text) <= limit else _ellipsis(text, text[: limit - 1])


def _shorten_bytes(text: str, limit: int) -> str:
    if len(text.encode("utf-8")) <= limit:
        return text
    if limit < len(ELLIPSIS.encode("utf-8")):
        return ""
    prefix = text.encode("utf-8")[: limit - 3].decode("utf-8", errors="ignore")
    return _ellipsis(text, prefix)


def _chapter_items(chapters: Iterable[Chapter]) -> list[Chapter]:
    try:
        return list(chapters)
    except TypeError:
        raise ChapterError("Chapters must be an iterable of Chapter values.") from None


def check_chapters(
    chapters: Iterable[Chapter], duration: float, settings: MetadataSettings
) -> list[Chapter]:
    """Return cleaned chapters, moving an early first chapter to zero; never sort input."""
    try:
        duration = _seconds(duration, "Video duration", positive=True)
    except MetadataTextError as error:
        raise ChapterError(str(error)) from None
    items = _chapter_items(chapters)
    if len(items) < settings.min_chapters:
        raise ChapterError(f"At least {settings.min_chapters} chapters are required.")
    minimum = settings.min_chapter_seconds
    result = []
    previous = -1.0
    for index, chapter in enumerate(items, 1):
        if not isinstance(chapter, Chapter):
            raise ChapterError(f"Chapter {index} must be a Chapter value.")
        try:
            start = _seconds(chapter.start, f"Chapter {index} start")
            title = _line(chapter.title)
        except MetadataTextError as error:
            raise ChapterError(str(error)) from None
        if start >= duration:
            raise ChapterError(f"Chapter {index} starts outside the video.")
        if start <= previous:
            raise ChapterError(f"Chapter {index} is not in strictly ascending order.")
        previous = start
        if not title:
            raise ChapterError(f"Chapter {index} title must not be empty.")
        title = _shorten_chars(title, settings.max_chapter_title_chars)
        result.append(Chapter(start, title))
    if result[0].start > minimum:
        raise ChapterError(f"The first chapter must start within the first {minimum:g} seconds.")
    result[0] = Chapter(0.0, result[0].title)
    for index, chapter in enumerate(result):
        end = result[index + 1].start if index + 1 < len(result) else duration
        length = end - chapter.start
        published_end = math.floor(end) if index + 1 < len(result) else duration
        published_length = published_end - math.floor(chapter.start)
        if (
            length < minimum and not math.isclose(length, minimum, rel_tol=0.0, abs_tol=1e-9)
        ) or published_length < minimum:
            raise ChapterError(
                f"Chapter {index + 1} must last at least {minimum:g} seconds, "
                "including after timestamp flooring."
            )
    return result


def _render(template: str, fields: Mapping[str, Any], allowed: frozenset[str]) -> str:
    formatter, parts = string.Formatter(), []
    try:
        check_template(template, allowed)
        for literal, name, spec, conversion in formatter.parse(template):
            parts.append(literal)
            if name is None:
                continue
            if name not in allowed or "{" in spec or "}" in spec:
                raise ValueError("only simple approved fields are supported")
            value = fields.get(name)
            if value is None or value == "":
                parts.append("")
                continue
            if conversion:
                value = formatter.convert_field(value, conversion)
            parts.append(format(value, spec))
        return _clean("".join(parts))
    except ValueError, TypeError, KeyError, AttributeError:
        raise MetadataTextError("Invalid metadata template or field format.") from None


def render_title(
    settings: MetadataSettings,
    *,
    group: str,
    lesson: int | str | None,
    date: Date | str | None,
    topic: str = "",
) -> str:
    """Render a one-line title, limited by Unicode characters and whole-word truncation."""
    fields = {"group": group, "lesson": lesson, "date": date, "topic": topic}
    text = _line(_render(settings.title_template, fields, TITLE_FIELDS | {"topic"}))
    if not text:
        raise MetadataTextError("Metadata title must not be empty.")
    return _shorten_chars(text, TITLE_LIMIT)


def _paragraphs(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


def build_description(
    *,
    summary: str,
    chapters: Iterable[Chapter],
    duration: float,
    footer_fields: Mapping[str, Any],
    settings: MetadataSettings,
) -> DescriptionResult:
    """Fit summary, intact chapters and footer into the UTF-8 description byte limit."""
    duration = _seconds(duration, "Video duration", positive=True)
    summary = _line(summary)
    items = _chapter_items(chapters)
    valid = check_chapters(items, duration, settings) if items else []
    chapter_text = "\n".join(
        f"{format_timestamp(chapter.start, long=duration >= 3600)} {chapter.title}"
        for chapter in valid
    )
    footer = _render(settings.description_footer, footer_fields, FOOTER_FIELDS)
    footer = "\n".join(" ".join(line.split()) for line in footer.splitlines()).strip()
    flags = []
    if len(chapter_text.encode("utf-8")) > DESCRIPTION_LIMIT:
        chapter_text = ""
        flags.append("description_chapters_dropped")
    # After sacrificing summary space, shorten the footer only if the fixed parts still overflow.
    fixed = _paragraphs(chapter_text, footer)
    if len(fixed.encode("utf-8")) > DESCRIPTION_LIMIT:
        budget = max(
            0, DESCRIPTION_LIMIT - len(chapter_text.encode("utf-8")) - (2 if chapter_text else 0)
        )
        footer = _shorten_bytes(footer, budget)
        flags.append("description_footer_truncated")
        fixed = _paragraphs(chapter_text, footer)
    summary_budget = max(0, DESCRIPTION_LIMIT - len(fixed.encode("utf-8")) - (2 if fixed else 0))
    shortened = _shorten_bytes(summary, summary_budget)
    if shortened != summary:
        flags.append("description_summary_truncated")
    return DescriptionResult(_paragraphs(shortened, chapter_text, footer), tuple(flags))
