"""The transcript format every stage shares (T-301; FR-13, FR-14).

A ``Transcription`` is a language plus ordered ``Segment``s; each segment has start and end
times in seconds, its text and (optionally) word timings. The same format is stored in the
``transcripts`` table (``raw``: timeline of the original recording, ``clean``: timeline of the
cleaned video) and in JSON files. Words are stored as compact ``[start, end, text, p]`` lists,
because a three-hour lesson has tens of thousands of them. Times are kept to the millisecond.
"""

import json
import math
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reel.db import Transcript, TranscriptKind

SCHEMA = 1


class TranscriptError(ValueError):
    """Invalid transcript data."""


def _time(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise TranscriptError(f"{what} must be a finite number, not {value!r}")
    if value < 0:
        raise TranscriptError(f"{what} must not be negative ({value})")
    return float(value)


def _optional(value: Any, what: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise TranscriptError(f"{what} must be a finite number or null, not {value!r}")
    return float(value)


def _ms(value: float) -> float:
    return round(value, 3)


@dataclass(frozen=True, slots=True)
class Word:
    start: float
    end: float
    text: str
    probability: float | None = None

    def __post_init__(self) -> None:
        start, end = _time(self.start, "word start"), _time(self.end, "word end")
        if end < start:
            raise TranscriptError(f"word {self.text!r} ends ({end}) before it starts ({start})")
        _optional(self.probability, "word probability")


@dataclass(frozen=True, slots=True)
class Segment:
    start: float
    end: float
    text: str
    words: tuple[Word, ...] = ()
    # Whisper's own confidence values, used against hallucinated text (R-4); None otherwise.
    avg_logprob: float | None = None
    no_speech_prob: float | None = None

    def __post_init__(self) -> None:
        start, end = _time(self.start, "segment start"), _time(self.end, "segment end")
        if end < start:
            raise TranscriptError(f"segment {self.text!r} ends ({end}) before it starts ({start})")
        if not isinstance(self.text, str):
            raise TranscriptError("segment text must be a string")
        _optional(self.avg_logprob, "avg_logprob")
        _optional(self.no_speech_prob, "no_speech_prob")

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"start": _ms(self.start), "end": _ms(self.end), "text": self.text}
        if self.words:
            data["words"] = [
                [_ms(w.start), _ms(w.end), w.text]
                + ([] if w.probability is None else [round(w.probability, 3)])
                for w in self.words
            ]
        for key in ("avg_logprob", "no_speech_prob"):
            if (value := getattr(self, key)) is not None:
                data[key] = round(value, 4)
        return data

    @classmethod
    def from_dict(cls, data: Any) -> Segment:
        if not isinstance(data, dict):
            raise TranscriptError(f"a segment must be an object, not {type(data).__name__}")
        try:
            words = tuple(Word(*item) for item in data.get("words") or ())
            return cls(
                data["start"],
                data["end"],
                data["text"],
                words,
                data.get("avg_logprob"),
                data.get("no_speech_prob"),
            )
        except KeyError as exc:
            raise TranscriptError(f"a segment needs {exc.args[0]!r}") from None
        except TypeError as exc:  # a word list of the wrong length
            raise TranscriptError(f"invalid word in segment: {exc}") from None


@dataclass(frozen=True)
class Transcription:
    language: str
    segments: tuple[Segment, ...]
    model: str | None = None
    details: dict[str, Any] = field(default_factory=dict)  # engine settings, timings, ...

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", tuple(self.segments))
        for before, after in zip(self.segments, self.segments[1:], strict=False):
            if after.start < before.start:
                raise TranscriptError(
                    f"segments are not in time order ({after.start} after {before.start})"
                )

    @property
    def text(self) -> str:
        return " ".join(segment.text.strip() for segment in self.segments if segment.text.strip())

    @property
    def end(self) -> float:
        return max((segment.end for segment in self.segments), default=0.0)

    def between(self, start: float, end: float) -> tuple[Segment, ...]:
        """Segments that overlap ``[start, end)``."""
        return tuple(s for s in self.segments if s.start < end and s.end > start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "language": self.language,
            "model": self.model,
            "details": self.details,
            "segments": [segment.to_dict() for segment in self.segments],
        }

    @classmethod
    def from_dict(cls, data: Any) -> Transcription:
        if not isinstance(data, dict):
            raise TranscriptError("a transcript must be a JSON object")
        if data.get("schema") != SCHEMA:
            raise TranscriptError(f"unsupported transcript schema {data.get('schema')!r}")
        segments = data.get("segments")
        if not isinstance(segments, list) or not isinstance(data.get("language"), str):
            raise TranscriptError("a transcript needs 'language' and a 'segments' list")
        return cls(
            data["language"],
            tuple(Segment.from_dict(item) for item in segments),
            data.get("model"),
            dict(data.get("details") or {}),
        )

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )

    @classmethod
    def from_json(cls, text: str) -> Transcription:
        try:
            return cls.from_dict(json.loads(text))
        except json.JSONDecodeError as exc:
            raise TranscriptError(f"not valid JSON: {exc}") from None


def write_json(path: Path, transcription: Transcription) -> Path:
    """Write UTF-8 JSON atomically (``.part`` then rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_text(transcription.to_json(), encoding="utf-8")
    os.replace(partial, path)
    return path


def read_json(path: Path) -> Transcription:
    return Transcription.from_json(Path(path).read_text(encoding="utf-8"))


def from_row(row: Transcript) -> Transcription:
    """The ``Transcription`` stored in a database row."""
    return Transcription(
        row.language,
        tuple(Segment.from_dict(item) for item in row.segments),
        row.model,
        dict(row.details or {}),
    )


def save_transcript(
    session: Session,
    job_id: int,
    kind: TranscriptKind,
    transcription: Transcription,
    *,
    cut_list_id: int | None = None,
) -> Transcript:
    """Insert or replace the job's transcript of this kind (a re-run replaces it)."""
    row = session.scalars(
        select(Transcript).where(Transcript.job_id == job_id, Transcript.kind == kind)
    ).one_or_none()
    if row is None:
        row = Transcript(job_id=job_id, kind=kind)
        session.add(row)
    data = transcription.to_dict()
    row.language = transcription.language
    row.text = transcription.text
    row.segments = data["segments"]
    row.model = transcription.model
    row.details = data["details"]
    row.cut_list_id = cut_list_id
    return row


def load_transcript(session: Session, job_id: int, kind: TranscriptKind) -> Transcription | None:
    row = session.scalars(
        select(Transcript).where(Transcript.job_id == job_id, Transcript.kind == kind)
    ).one_or_none()
    return None if row is None else from_row(row)


def words(segments: Iterable[Segment]) -> list[Word]:
    """All words in order; segments without word timings count as one word each."""
    found: list[Word] = []
    for segment in segments:
        found.extend(segment.words or (Word(segment.start, segment.end, segment.text),))
    return found
