"""Group, lesson number and date of a recording (T-203; FR-3, D-018).

Convention: ``<watched folder>/<group folder>/<file>``. The group is the folder name (letter
case, spaces, ``_`` and ``-`` ignored; aliases in groups.toml), the lesson number comes from
the file name through ``ingest.lesson_number_patterns``, and the date from the file name
(``ingest.date_patterns``), else the media creation time, else the file time, as a local date in
``schedule.timezone``.

Nothing here raises for unexpected names: what cannot be found becomes a flag. The job still
runs the heavy stages and holds before Chapters until staff fill the value in (D-018).
"""

import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from reel.config.groups import GroupsFile
from reel.config.loader import LoadedConfig
from reel.config.schema import IngestSettings
from reel.db import Job, Recording, utcnow
from reel.ingest.watcher import path_key

UNKNOWN_GROUP = "unknown_group"
MISSING_LESSON = "missing_lesson_number"
AMBIGUOUS_LESSON = "ambiguous_lesson_number"
MISSING_DATE = "missing_date"
FLAGS = (UNKNOWN_GROUP, MISSING_LESSON, AMBIGUOUS_LESSON, MISSING_DATE)

# Encoders write 1970 or 1904 when the clock is unknown; such creation times are not dates.
EARLIEST_MEDIA_TIME = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Identification:
    group: str | None
    lesson_number: int | None
    recording_date: date | None
    flags: tuple[str, ...] = ()
    # How each value was found (stored in Recording.identification for the dashboard).
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.flags

    def apply(self, recording: Recording, job: Job | None = None) -> None:
        """Store the result on the recording and add the flags to the job."""
        recording.group_name = self.group
        recording.lesson_number = self.lesson_number
        recording.recording_date = self.recording_date
        recording.identification = {**self.details, "flags": list(self.flags)}
        if job is not None:
            for flag in self.flags:
                job.add_flag(flag)


@lru_cache(maxsize=64)
def _regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def folders_below_watch_folder(path: Path, watch_folders: Iterable[Path]) -> list[str]:
    """Folder names between the (outermost) watched folder and the file, nearest first.

    A file outside every watched folder (e.g. imported by hand) uses its parent folder.
    """
    parent = Path(os.path.abspath(path)).parent
    key = path_key(parent)
    roots = [
        root
        for root in map(path_key, watch_folders)
        if key == root or key.startswith(root.rstrip(os.sep) + os.sep)
    ]
    if not roots:
        return [parent.name] if parent.name else []
    depth = len(Path(min(roots, key=len)).parts)
    return list(reversed(parent.parts[depth:]))


def find_group(folders: list[str], groups: GroupsFile) -> str | None:
    for name in folders:  # nearest first, so <group>/<sub folder>/<file> also works
        found = groups.find(name)
        if found:
            return found[0]
    return None


def find_lesson_numbers(text: str, patterns: Iterable[str]) -> dict[int, str]:
    """Every lesson number the patterns find: number → the matched text."""
    found: dict[int, str] = {}
    for pattern in patterns:
        for match in _regex(pattern).finditer(text):
            found.setdefault(int(match.group(1)), match.group(0).strip())
    return found


def find_date(text: str, patterns: Iterable[str]) -> tuple[date, str] | None:
    """The first real calendar date the patterns find (2026-02-30 is skipped)."""
    for pattern in patterns:
        for match in _regex(pattern).finditer(text):
            try:
                day = date(int(match["year"]), int(match["month"]), int(match["day"]))
            except ValueError:
                continue
            return day, match.group(0)
    return None


def _aware(moment: datetime | None) -> datetime | None:
    """Times without a zone (some encoders write them) count as UTC."""
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def identify(
    path: Path,
    *,
    watch_folders: Iterable[Path],
    groups: GroupsFile,
    ingest: IngestSettings,
    timezone: ZoneInfo,
    creation_time: datetime | None = None,
    file_modified_at: datetime | None = None,
    now: datetime | None = None,
) -> Identification:
    """Identify one recording. ``creation_time`` is the media's own tag (ffprobe)."""
    now = now or utcnow()
    stem = unicodedata.normalize("NFC", Path(path).stem)
    folders = [
        unicodedata.normalize("NFC", name)
        for name in folders_below_watch_folder(Path(path), watch_folders)
    ]
    flags: list[str] = []
    details: dict[str, Any] = {"file_name": Path(path).name, "folders": folders}

    group = find_group(folders, groups)
    details["group_source"] = "folder" if group else None
    if group is None:
        flags.append(UNKNOWN_GROUP)

    numbers = find_lesson_numbers(stem, ingest.lesson_number_patterns)
    lesson = next(iter(numbers)) if len(numbers) == 1 else None
    details["lesson_source"] = "file name" if lesson is not None else None
    details["lesson_matches"] = list(numbers.values())
    if not numbers:
        flags.append(MISSING_LESSON)
    elif lesson is None:
        flags.append(AMBIGUOUS_LESSON)

    recording_date, date_source = None, None
    creation_time, file_modified_at = _aware(creation_time), _aware(file_modified_at)
    named = find_date(stem, ingest.date_patterns)
    if named:
        recording_date, date_source = named[0], "file name"
        details["date_match"] = named[1]
    elif creation_time and EARLIEST_MEDIA_TIME <= creation_time <= now + timedelta(days=1):
        recording_date = creation_time.astimezone(timezone).date()
        date_source = "media creation time"
    elif file_modified_at:
        recording_date, date_source = file_modified_at.astimezone(timezone).date(), "file time"
    else:
        flags.append(MISSING_DATE)
    details["date_source"] = date_source
    return Identification(group, lesson, recording_date, tuple(flags), details)


def identify_recording(
    cfg: LoadedConfig, recording: Recording, *, creation_time: datetime | None = None
) -> Identification:
    """:func:`identify` with the paths, groups and settings of the loaded configuration."""
    return identify(
        Path(recording.source_path),
        watch_folders=cfg.paths.watch_folders,
        groups=cfg.groups,
        ingest=cfg.settings.ingest,
        timezone=ZoneInfo(cfg.settings.schedule.timezone),
        creation_time=creation_time,
        file_modified_at=recording.file_modified_at,
    )
