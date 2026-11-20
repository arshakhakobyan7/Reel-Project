"""Library publication types and validation shared by both adapters (T-705)."""

import json
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date as Date
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit
from uuid import UUID

if TYPE_CHECKING:
    from reel.config.loader import LoadedConfig
    from reel.db import Database

MAX_JSON_BYTES = 1_048_576
_KEY = re.compile(r"reel-[0-9a-f]{32}-job-[1-9][0-9]*\Z")


class LibraryError(RuntimeError):
    """A sanitised publication failure; messages never include upstream bodies/secrets."""

    retryable: ClassVar[bool] = False

    def __init__(self, code: str, *, retry_after: float | None = None) -> None:
        self.code = code
        self.retry_after = retry_after
        message = (
            "LIBRARY_API_TOKEN is missing or invalid."
            if code == "missing_token"
            else f"Library publication failed: {code}."
        )
        super().__init__(message)


class RetryableLibraryError(LibraryError):
    """A temporary service/transport failure; retry with the same external key."""

    retryable = True


class PermanentLibraryError(LibraryError):
    """A configuration, input or HTTP client error requiring correction."""

    retryable = False


def _text(value: Any, maximum: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and bool(value.strip())


def _url(value: Any) -> bool:
    if not _text(value, 1024) or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.port != 0
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class Chapter:
    start_seconds: float
    title: str

    def __post_init__(self) -> None:
        try:
            finite = type(self.start_seconds) in {float, int} and math.isfinite(self.start_seconds)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("chapter start_seconds must be a finite number")
        if self.start_seconds < 0 or not _text(self.title, 200):
            raise ValueError("invalid chapter start or title")

    def to_dict(self) -> dict[str, Any]:
        return {"start_seconds": self.start_seconds, "title": self.title}


@dataclass(frozen=True)
class LibraryItem:
    """Immutable metadata; chapter dictionaries are accepted and copied into Chapter values."""

    title: str
    video_url: str
    group: str
    lesson_number: int | None = None
    recording_date: Date | None = None
    chapters: Sequence[Chapter | Mapping[str, Any]] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not _text(self.title, 200) or not _text(self.group, 200) or not _url(self.video_url):
            raise ValueError("invalid library title, group or video_url")
        if self.lesson_number is not None and (
            type(self.lesson_number) is not int or self.lesson_number < 0
        ):
            raise ValueError("lesson_number must be a nonnegative integer or null")
        if self.recording_date is not None and type(self.recording_date) is not Date:
            raise ValueError("recording_date must be a date or null")
        if not isinstance(self.chapters, (list, tuple)):
            raise ValueError("chapters must be a list or tuple")
        chapters = []
        for value in self.chapters:
            if isinstance(value, Chapter):
                chapter = value
            elif isinstance(value, Mapping) and set(value) == {"start_seconds", "title"}:
                chapter = Chapter(value["start_seconds"], value["title"])
            else:
                raise ValueError("invalid chapter fields")
            if chapters and chapter.start_seconds <= chapters[-1].start_seconds:
                raise ValueError("chapter starts must strictly increase")
            chapters.append(chapter)
        object.__setattr__(self, "chapters", tuple(chapters))

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "video_url": self.video_url,
            "group": self.group,
            "lesson_number": self.lesson_number,
            "recording_date": self.recording_date.isoformat() if self.recording_date else None,
            "chapters": [chapter.to_dict() for chapter in self.chapters],
        }

    @classmethod
    def from_dict(cls, value: Any) -> LibraryItem:
        """Validate the exact JSON contract, including canonical ISO dates."""
        keys = {"title", "video_url", "group", "lesson_number", "recording_date", "chapters"}
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError("invalid library item fields")
        raw_date = value["recording_date"]
        if raw_date is not None and not isinstance(raw_date, str):
            raise ValueError("invalid recording date")
        recording_date = Date.fromisoformat(raw_date) if raw_date is not None else None
        if recording_date is not None and recording_date.isoformat() != raw_date:
            raise ValueError("recording date must use YYYY-MM-DD")
        return cls(**{**value, "recording_date": recording_date})


@dataclass(frozen=True)
class LibraryResult:
    """A real entry identity, or an explicit dry-run preview with no invented id/URL."""

    entry_id: str | None
    url: str | None
    created: bool
    external_key: str | None = None
    dry_run: bool = False
    payload: dict[str, Any] | None = None

    @property
    def updated(self) -> bool:
        return not self.dry_run and self.entry_id is not None and not self.created


class LibraryPublisher(ABC):
    name: ClassVar[str]

    @abstractmethod
    def publish(self, job_id: int, item: LibraryItem) -> LibraryResult:
        """Create or update one entry for this job."""


def _job_id(job_id: int) -> None:
    if type(job_id) is not int or job_id < 1:
        raise PermanentLibraryError("invalid_job_id")


def external_key(instance_id: str, job_id: int) -> str:
    """A stable, URL-safe key namespaced by the database's persistent UUID."""
    _job_id(job_id)
    try:
        if not isinstance(instance_id, str):
            raise ValueError("missing UUID")
        instance = UUID(instance_id).hex
    except ValueError:
        raise PermanentLibraryError("invalid_instance_id") from None
    return f"reel-{instance}-job-{job_id}"


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("nonfinite JSON number")


def _decode_json(raw: bytes) -> Any:
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("library JSON exceeds 1 MiB")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)


def make_library_publisher(
    cfg: LoadedConfig, db: Database, *, dry_run: bool = False
) -> LibraryPublisher:
    """Choose from config; read the HTTP namespace from this database, never generate one."""
    from reel.db import AppMeta
    from reel.library.http import HttpLibraryPublisher
    from reel.library.local import LocalLibraryPublisher

    settings = cfg.settings.library
    if settings.provider == "local":
        return LocalLibraryPublisher(db, dry_run=dry_run)
    with db.transaction() as session:
        meta = session.get(AppMeta, "instance_id")
        if meta is None:
            raise PermanentLibraryError("missing_instance_id")
        instance_id = meta.value
    return HttpLibraryPublisher(
        settings.http_base_url,
        instance_id=instance_id,
        token=cfg.secrets.get("LIBRARY_API_TOKEN"),
        timeout_seconds=settings.timeout_seconds,
        dry_run=dry_run,
    )
