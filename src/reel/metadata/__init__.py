"""Deterministic metadata text and reusable chapter rules."""

from reel.metadata.text import (
    Chapter,
    ChapterError,
    DescriptionResult,
    MetadataTextError,
    build_description,
    check_chapters,
    format_timestamp,
    render_title,
)

__all__ = [
    "Chapter",
    "ChapterError",
    "DescriptionResult",
    "MetadataTextError",
    "build_description",
    "check_chapters",
    "format_timestamp",
    "render_title",
]
