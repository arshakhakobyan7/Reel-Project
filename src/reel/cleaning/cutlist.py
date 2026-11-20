"""The cut list: one exact description of what is kept (T-404; FR-9, FR-10, FR-12).

All analyses report what to remove on the **raw timeline**: the parts of a lesson one after
another (D-035). This module merges those removals into ordered keep/remove segments that cover
the whole timeline, and maps times between the raw and the cleaned timeline in both directions.

Rules, in this order:
1. Removals are clipped to the timeline. Only the lesson-boundary cuts are padded
   (``padding_seconds`` of the lesson is kept next to them); silence and empty-board removals are
   already shaped by their own rules (D-014, D-040: "shortened to 5 s" stays 5 s).
2. Overlapping or touching removals are merged; their reasons are joined.
3. A removal shorter than ``min_remove_seconds`` is dropped (not worth a jump cut), unless it
   touches the start or the end of the timeline.
4. A kept piece shorter than ``min_keep_seconds`` between removals (or at an edge) is removed too.
5. Every boundary is put on a video frame of the part it falls in.
6. Segments are split at part boundaries, so each one lies inside one file (the renderer trims
   each part separately).
"""

import bisect
import json
import math
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reel.cleaning.boundaries import LessonBoundaries
from reel.config.schema import CutSettings
from reel.db.enums import CutAction
from reel.db.models import CutList as CutListRow

SCHEMA = 1
EPSILON = 1e-6
BEFORE = "before the lesson"
AFTER = "after the lesson"


class CutListError(ValueError):
    """The removals cannot give a usable video (e.g. nothing would be kept)."""


@dataclass(frozen=True)
class Part:
    """One recorded file of the lesson, in recording order."""

    duration: float
    fps: float | None = None  # None: boundaries are not frame-aligned in this part
    name: str = ""

    def __post_init__(self) -> None:
        if not (math.isfinite(self.duration) and self.duration > 0):
            raise ValueError("a part needs a positive duration")
        if self.fps is not None and not (math.isfinite(self.fps) and self.fps > 0):
            raise ValueError("fps must be positive")


class Timeline:
    """The raw timeline: the parts one after another."""

    def __init__(self, parts: Sequence[Part]) -> None:
        if not parts:
            raise ValueError("a timeline needs at least one part")
        self.parts = tuple(parts)
        self.offsets: list[float] = []
        total = 0.0
        for part in self.parts:
            self.offsets.append(total)
            total += part.duration
        self.duration = total

    def part_at(self, seconds: float) -> int:
        """Index of the part that contains ``seconds`` (a boundary belongs to the later part)."""
        index = bisect.bisect_right(self.offsets, seconds) - 1
        return min(max(index, 0), len(self.parts) - 1)

    def to_raw(self, part: int, seconds: float) -> float:
        """A time inside one part → the raw timeline."""
        return self.offsets[part] + seconds

    def locate(self, seconds: float) -> tuple[int, float]:
        """A raw time → (part index, time inside that part)."""
        index = self.part_at(seconds)
        return index, seconds - self.offsets[index]

    def snap(self, seconds: float) -> float:
        """Put a raw time on the nearest frame of its part (part edges stay where they are)."""
        index, local = self.locate(seconds)
        fps = self.parts[index].fps
        if fps is None:
            return seconds
        frame = round(local * fps) / fps
        return self.offsets[index] + min(frame, self.parts[index].duration)


@dataclass(frozen=True)
class Cut:
    """A removal request on the raw timeline."""

    start: float
    end: float
    reason: str
    pad: bool = False  # keep ``padding_seconds`` of content next to the cut


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    action: CutAction
    reason: str
    part: int

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_row(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 6),
            "end": round(self.end, 6),
            "action": self.action.value,
            "reason": self.reason,
            "part": self.part,
        }


@dataclass(frozen=True)
class CutList:
    segments: tuple[Segment, ...]  # cover the whole raw timeline, in order
    original_duration: float
    parameters: dict[str, Any] = field(default_factory=dict)
    parts: tuple[Part, ...] = ()

    def __post_init__(self) -> None:
        kept, starts, clean = [], [], 0.0
        for segment in self.segments:
            if segment.action is CutAction.KEEP:
                kept.append(segment)
                starts.append(clean)
                clean += segment.duration
        object.__setattr__(self, "_kept", tuple(kept))
        object.__setattr__(self, "_clean_starts", starts)
        object.__setattr__(self, "_raw_starts", [s.start for s in kept])

    @property
    def kept(self) -> tuple[Segment, ...]:
        return self._kept

    @property
    def removed(self) -> tuple[Segment, ...]:
        return tuple(s for s in self.segments if s.action is CutAction.REMOVE)

    @property
    def final_duration(self) -> float:
        return sum(s.duration for s in self.kept)

    def to_clean(self, raw: float) -> float:
        """Raw time → cleaned time. A time inside a removed part maps to the cut (the start of
        the next kept piece), so the mapping never goes backwards."""
        index = bisect.bisect_right(self._raw_starts, raw) - 1
        if index < 0:
            return 0.0
        segment = self.kept[index]
        return self._clean_starts[index] + min(max(raw - segment.start, 0.0), segment.duration)

    def to_raw(self, clean: float) -> float:
        """Cleaned time → raw time. At a join the time of the later piece is given."""
        if not self.kept:
            raise CutListError("nothing is kept")
        clean = min(max(clean, 0.0), self.final_duration)
        index = max(bisect.bisect_right(self._clean_starts, clean) - 1, 0)
        segment = self.kept[index]
        return segment.start + min(clean - self._clean_starts[index], segment.duration)

    def keeps(self, raw_start: float, raw_end: float) -> float:
        """How many seconds of ``[raw_start, raw_end)`` are kept."""
        return self.to_clean(raw_end) - self.to_clean(raw_start)

    def to_rows(self) -> list[dict[str, Any]]:
        return [s.to_row() for s in self.segments]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "original_duration": round(self.original_duration, 6),
            "final_duration": round(self.final_duration, 6),
            "parts": [{"duration": p.duration, "fps": p.fps, "name": p.name} for p in self.parts],
            "segments": self.to_rows(),
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CutList:
        if data.get("schema") != SCHEMA:
            raise CutListError(f"unknown cut list schema {data.get('schema')!r}")
        segments = tuple(
            Segment(
                float(s["start"]),
                float(s["end"]),
                CutAction(s["action"]),
                str(s["reason"]),
                int(s.get("part", 0)),
            )
            for s in data["segments"]
        )
        parts = tuple(
            Part(float(p["duration"]), p.get("fps"), p.get("name", "")) for p in data["parts"]
        )
        return cls(segments, float(data["original_duration"]), dict(data["parameters"]), parts)


def boundary_cuts(boundaries: LessonBoundaries, duration: float) -> list[Cut]:
    """The removals before and after the lesson (T-402); a missing boundary cuts nothing."""
    cuts = []
    if boundaries.start is not None and boundaries.start.seconds > 0:
        cuts.append(Cut(0.0, boundaries.start.seconds, BEFORE, pad=True))
    if boundaries.end is not None and boundaries.end.seconds < duration:
        cuts.append(Cut(boundaries.end.seconds, duration, AFTER, pad=True))
    return cuts


def _merge(cuts: Iterable[tuple[float, float, str]]) -> list[list[Any]]:
    merged: list[list[Any]] = []
    for start, end, reason in sorted(cuts):
        if merged and start <= merged[-1][1] + EPSILON:
            last = merged[-1]
            last[1] = max(last[1], end)
            if reason not in last[2]:
                last[2].append(reason)
        else:
            merged.append([start, end, [reason]])
    return merged


def build_cut_list(timeline: Timeline, cuts: Iterable[Cut], settings: CutSettings) -> CutList:
    """Merge the removals into keep/remove segments covering the whole raw timeline."""
    total = timeline.duration
    padding = settings.padding_seconds
    shaped = []
    for cut in cuts:
        start, end = max(cut.start, 0.0), min(cut.end, total)
        if cut.pad:
            start = start if start <= EPSILON else start + padding
            end = end if end >= total - EPSILON else end - padding
        if end - start > EPSILON:
            shaped.append((start, end, cut.reason))
    removals = []
    for start, end, reasons in _merge(shaped):
        at_edge = start <= EPSILON or end >= total - EPSILON
        if end - start >= settings.min_remove_seconds or at_edge:
            removals.append([start, end, reasons])
    # Kept pieces that are too short are removed with their neighbours.
    keeps = []
    position = 0.0
    for start, end, _ in removals:
        keeps.append((position, start))
        position = end
    keeps.append((position, total))
    short = [
        (a, b, "too short to keep") for a, b in keeps if EPSILON < b - a < settings.min_keep_seconds
    ]
    if short:
        removals = _merge([(a, b, r) for a, b, rs in removals for r in rs] + short)
    # Frame alignment, then the segment list.
    segments: list[tuple[float, float, CutAction, str]] = []
    position = 0.0
    for start, end, reasons in removals:
        start, end = max(timeline.snap(start), position), timeline.snap(end)
        if end - start <= EPSILON:
            continue
        if start - position > EPSILON:
            segments.append((position, start, CutAction.KEEP, "lesson"))
        else:
            start = position  # a sliver left by frame alignment is removed with the cut
        segments.append((start, end, CutAction.REMOVE, "; ".join(reasons)))
        position = end
    if total - position > EPSILON:
        segments.append((position, total, CutAction.KEEP, "lesson"))
    result = _split_at_parts(timeline, segments)
    cut_list = CutList(
        tuple(result),
        total,
        {
            "padding_seconds": padding,
            "min_keep_seconds": settings.min_keep_seconds,
            "min_remove_seconds": settings.min_remove_seconds,
        },
        timeline.parts,
    )
    if not cut_list.kept:
        raise CutListError("the removals cover the whole recording; nothing would be kept")
    return cut_list


def _split_at_parts(
    timeline: Timeline, segments: list[tuple[float, float, CutAction, str]]
) -> list[Segment]:
    result = []
    edges = timeline.offsets[1:]
    for start, end, action, reason in segments:
        points = [start, *(e for e in edges if start + EPSILON < e < end - EPSILON), end]
        for a, b in zip(points, points[1:], strict=False):
            result.append(Segment(a, b, action, reason, timeline.part_at(a)))
    return result


def write_cut_list(path: Path, cut_list: CutList) -> Path:
    """Write ``cutlist.json`` atomically (``.part`` then rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_text(json.dumps(cut_list.to_dict(), ensure_ascii=False, indent=1), "utf-8")
    os.replace(partial, path)
    return path


def read_cut_list(path: Path) -> CutList:
    return CutList.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def save_cut_list(session: Session, job_id: int, cut_list: CutList) -> CutListRow:
    """Store a new version (a re-run adds a version; older ones stay for comparison)."""
    latest = session.scalar(select(func.max(CutListRow.version)).where(CutListRow.job_id == job_id))
    row = CutListRow(
        job_id=job_id,
        version=(latest or 0) + 1,
        original_duration_seconds=round(cut_list.original_duration, 6),
        final_duration_seconds=round(cut_list.final_duration, 6),
        segments=cut_list.to_rows(),
        parameters={
            **cut_list.parameters,
            "schema": SCHEMA,
            "parts": cut_list.to_dict()["parts"],
        },
    )
    session.add(row)
    return row


def load_cut_list(row: CutListRow) -> CutList:
    parameters = dict(row.parameters)
    parts = parameters.pop("parts", [])
    parameters.pop("schema", None)
    return CutList.from_dict(
        {
            "schema": SCHEMA,
            "original_duration": row.original_duration_seconds,
            "segments": row.segments,
            "parts": parts,
            "parameters": parameters,
        }
    )
