"""Tables for the PRD entities (§8.2), the job queue, the folder watcher and staff (T-101).

| PRD entity    | Table(s)                                            |
|---------------|-----------------------------------------------------|
| Recording     | ``recordings``                                      |
| Job           | ``jobs`` + ``stage_runs`` (stage timestamps, errors) |
| CutList       | ``cut_lists`` (versioned: a re-run adds a version)  |
| Transcript    | ``transcripts`` (raw and clean timeline)            |
| VideoMetadata | ``video_metadata``                                  |
| ConsentCheck  | ``consent_checks``                                  |
| Publication   | ``publication_targets``, one row per target (FR-29) |

Plus ``job_events`` (audit), ``library_entries`` (interim Academy Library, D-008),
``seen_files`` (watcher state), ``staff_users`` (dashboard login) and the bookkeeping
tables ``schema_versions`` and ``app_meta``. Conventions are in D-027.
"""

import math
import re
import unicodedata
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from reel.db.base import JSON_VALUE, Base, enum_type, utcnow
from reel.db.enums import (
    ActorKind,
    ConsentResult,
    CutAction,
    JobStatus,
    PublicationStatus,
    SeenFileState,
    Stage,
    StageRunStatus,
    TargetKind,
    TranscriptKind,
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_USERNAME = re.compile(r"[\w.@+-]{2,64}")

# Every table that holds ids used outside the database (folder names, log lines,
# idempotency tags on YouTube/Slack) uses AUTOINCREMENT, so SQLite never reuses an id
# after a row is deleted.
_NO_ID_REUSE = {"sqlite_autoincrement": True}
ROLE_ADMIN, ROLE_USER = "admin", "user"  # T-1013


def _created() -> Mapped[datetime]:
    return mapped_column(default=utcnow)


def _updated() -> Mapped[datetime]:
    return mapped_column(default=utcnow, onupdate=utcnow)


def _job_fk(*, unique: bool = False) -> Mapped[int]:
    return mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=not unique, unique=unique)


def _children(order_by: str) -> Any:
    """One-to-many from Job: deleted with the job, also by the database itself."""
    return relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True, order_by=order_by
    )


def _child() -> Any:
    return relationship(back_populates="job", cascade="all, delete-orphan", passive_deletes=True)


def normalize_username(name: str) -> str:
    """Canonical form of a staff username: NFC, trimmed, case-folded (``Anna`` = ``anna``)."""
    value = unicodedata.normalize("NFC", name).strip().casefold()
    if not _USERNAME.fullmatch(value):
        raise ValueError("username must be 2-64 letters, digits or . _ @ + -")
    return value


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def validate_segments(segments: Any) -> list[dict[str, Any]]:
    """Check the shape of a cut list (FR-9): ordered, non-overlapping, each with a reason."""
    if not isinstance(segments, list):
        raise ValueError("segments must be a list")
    actions = {action.value for action in CutAction}
    previous_end = 0.0
    for index, segment in enumerate(segments):
        where = f"segment {index}"
        if not isinstance(segment, dict):
            raise ValueError(f"{where}: expected an object")
        start, end = segment.get("start"), segment.get("end")
        if not (_number(start) and _number(end)):
            raise ValueError(f"{where}: start and end must be numbers")
        if start < 0 or end <= start:
            raise ValueError(f"{where}: needs 0 <= start < end (got {start} .. {end})")
        if start < previous_end:
            raise ValueError(f"{where}: overlaps the previous segment or is out of order")
        if segment.get("action") not in actions:
            raise ValueError(f"{where}: action must be one of {sorted(actions)}")
        reason = segment.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{where}: a reason is required")
        previous_end = end
    return segments


# --- Ingestion -------------------------------------------------------------------------------


class Recording(Base):
    """A lesson recording found in a watched folder (PRD Recording)."""

    __tablename__ = "recordings"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_not_negative"),
        CheckConstraint("lesson_number IS NULL OR lesson_number >= 0", name="lesson_not_negative"),
        CheckConstraint(
            "original_duration_seconds IS NULL OR original_duration_seconds >= 0",
            name="duration_not_negative",
        ),
        _NO_ID_REUSE,
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_path: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    file_modified_at: Mapped[datetime | None]
    # SHA-256 of the file (FR-4). Empty until the import stage has hashed the file; unique
    # once set, so the same content can never become two recordings.
    content_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("recordings.id", ondelete="SET NULL")
    )
    # T-1013: the account that uploaded it in the dashboard (None: a watched folder), and
    # whether it is published (YouTube, Slack, Classroom, Library) or only processed.
    uploaded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL"), index=True
    )
    publish: Mapped[bool] = mapped_column(default=True, server_default=true())
    group_name: Mapped[str | None] = mapped_column(String(200))
    lesson_number: Mapped[int | None]
    recording_date: Mapped[date | None]
    # Where group, lesson number and date came from, e.g. {"group": "folder"} (T-203).
    identification: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    original_duration_seconds: Mapped[float | None]
    media_info: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE)  # ffprobe summary
    detected_at: Mapped[datetime] = mapped_column(default=utcnow)  # first seen (M-2 start)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    job: Mapped[Job | None] = relationship(
        back_populates="recording", cascade="all, delete-orphan", passive_deletes=True
    )
    duplicate_of: Mapped[Recording | None] = relationship(remote_side=[id])

    @validates("content_hash")
    def _check_hash(self, _key: str, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if not _SHA256.fullmatch(value):
            raise ValueError("content_hash must be a SHA-256 hex digest (64 hex characters)")
        return value


class SeenFile(Base):
    """Folder watcher state for one file path (FR-1, FR-2)."""

    __tablename__ = "seen_files"
    __table_args__ = (CheckConstraint("size_bytes >= 0", name="size_not_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    modified_at: Mapped[datetime]  # the file's own modification time
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    stable_since: Mapped[datetime] = mapped_column(default=utcnow)
    last_checked_at: Mapped[datetime] = mapped_column(default=utcnow)
    state: Mapped[SeenFileState] = mapped_column(
        enum_type(SeenFileState), default=SeenFileState.WAITING
    )
    recording_id: Mapped[int | None] = mapped_column(
        ForeignKey("recordings.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)

    recording: Mapped[Recording | None] = relationship()


# --- Job queue -------------------------------------------------------------------------------


class Job(Base):
    """Processing of one recording through the pipeline (PRD Job; FR-5)."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
        Index("ix_jobs_queue", "status", "next_attempt_at"),
        _NO_ID_REUSE,
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # One job per recording; re-runs reuse the job (FR-32).
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), unique=True
    )
    stage: Mapped[Stage] = mapped_column(enum_type(Stage), default=Stage.IMPORT)
    status: Mapped[JobStatus] = mapped_column(enum_type(JobStatus), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(default=0)  # attempts of the current stage
    next_attempt_at: Mapped[datetime | None]  # retry backoff (FR-31)
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None]
    hold_reason: Mapped[str | None] = mapped_column(String(64))  # short code
    hold_message: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    # Things staff should look at, e.g. "boundaries_not_found" (D-005), "llm_fallback"
    # (FR-19). Replace the list to change it (see JSON_VALUE).
    flags: Mapped[list[str]] = mapped_column(JSON_VALUE, default=list)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    # Row version: an ORM update of a job that another process changed in the meantime
    # fails (StaleDataError) instead of silently overwriting the other change.
    version: Mapped[int] = mapped_column(default=1)

    __mapper_args__ = {"version_id_col": version}

    recording: Mapped[Recording] = relationship(back_populates="job")
    stage_runs: Mapped[list[StageRun]] = _children("StageRun.id")
    events: Mapped[list[JobEvent]] = _children("JobEvent.id")
    cut_lists: Mapped[list[CutList]] = _children("CutList.version")
    transcripts: Mapped[list[Transcript]] = _children("Transcript.id")
    video_metadata: Mapped[VideoMetadata | None] = _child()
    consent_checks: Mapped[list[ConsentCheck]] = _children("ConsentCheck.id")
    publications: Mapped[list[PublicationTarget]] = _children("PublicationTarget.id")
    library_entry: Mapped[LibraryEntry | None] = _child()

    def __repr__(self) -> str:
        return f"<Job id={self.id} stage={self.stage} status={self.status}>"

    def add_flag(self, flag: str) -> None:
        if flag not in self.flags:
            self.flags = [*self.flags, flag]

    @property
    def latest_cut_list(self) -> CutList | None:
        return max(self.cut_lists, key=lambda cut_list: cut_list.version, default=None)

    def transcript(self, kind: TranscriptKind) -> Transcript | None:
        return next((t for t in self.transcripts if t.kind == kind), None)

    def publication(self, target: TargetKind) -> PublicationTarget | None:
        return next((p for p in self.publications if p.target == target), None)

    def publication_summary(self) -> dict[str, str | None]:
        """The fields of the PRD's Publication entity (§8.2), from the per-target rows."""

        def value(target: TargetKind, attribute: str) -> str | None:
            row = self.publication(target)
            return getattr(row, attribute) if row else None

        youtube = self.publication(TargetKind.YOUTUBE)
        return {
            "youtube_video_id": value(TargetKind.YOUTUBE, "external_id"),
            "youtube_url": value(TargetKind.YOUTUBE, "url"),
            "playlist_id": youtube.details.get("playlist_id") if youtube else None,
            "slack_message_link": value(TargetKind.SLACK, "url"),
            "classroom_post_id": value(TargetKind.CLASSROOM, "external_id"),
            "library_entry_id": value(TargetKind.LIBRARY, "external_id"),
        }


class StageRun(Base):
    """One attempt of one stage: timestamps, outcome, error, compute metrics."""

    __tablename__ = "stage_runs"
    __table_args__ = (CheckConstraint("attempt >= 1", name="attempt_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = _job_fk()
    stage: Mapped[Stage] = mapped_column(enum_type(Stage))
    attempt: Mapped[int]
    status: Mapped[StageRunStatus] = mapped_column(
        enum_type(StageRunStatus), default=StageRunStatus.RUNNING
    )
    worker: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(Text)
    # Compute time, audio seconds, LLM tokens ... (NFR-7, NFR-8, D-020).
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)

    job: Mapped[Job] = relationship(back_populates="stage_runs")

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class JobEvent(Base):
    """Audit log entry: who did what to a job, and when."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = _job_fk()
    at: Mapped[datetime] = mapped_column(default=utcnow)
    kind: Mapped[str] = mapped_column(String(40))  # e.g. "held", "released" (T-102)
    actor_kind: Mapped[ActorKind] = mapped_column(enum_type(ActorKind), default=ActorKind.SYSTEM)
    actor_name: Mapped[str | None] = mapped_column(String(200))  # staff user or worker
    stage: Mapped[Stage | None] = mapped_column(enum_type(Stage))
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)

    job: Mapped[Job] = relationship(back_populates="events")


# --- Stage outputs ---------------------------------------------------------------------------


class CutList(Base):
    """Kept and removed segments of one cleaning run (PRD CutList; FR-9, FR-12)."""

    __tablename__ = "cut_lists"
    __table_args__ = (
        UniqueConstraint("job_id", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "original_duration_seconds >= 0 AND final_duration_seconds >= 0",
            name="durations_not_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(default=1)
    original_duration_seconds: Mapped[float]
    final_duration_seconds: Mapped[float]
    # [{"start": 0.0, "end": 312.4, "action": "remove", "reason": "before class"}, ...]
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE)
    # Thresholds and phrases used, so a wrong cut can be explained (UJ-5).
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = _created()

    job: Mapped[Job] = relationship(back_populates="cut_lists")

    @validates("segments")
    def _check_segments(self, _key: str, value: Any) -> list[dict[str, Any]]:
        return validate_segments(value)


class Transcript(Base):
    """Full text and timestamped segments (PRD Transcript; FR-13, FR-14)."""

    __tablename__ = "transcripts"
    __table_args__ = (UniqueConstraint("job_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    kind: Mapped[TranscriptKind] = mapped_column(enum_type(TranscriptKind))
    language: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    model: Mapped[str | None] = mapped_column(String(200))
    # For the clean transcript: the cut list its timestamps were mapped with.
    cut_list_id: Mapped[int | None] = mapped_column(ForeignKey("cut_lists.id", ondelete="SET NULL"))
    details: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    job: Mapped[Job] = relationship(back_populates="transcripts")
    cut_list: Mapped[CutList | None] = relationship()


class VideoMetadata(Base):
    """Title, description, chapters and thumbnail (PRD VideoMetadata; FR-15 ... FR-19)."""

    __tablename__ = "video_metadata"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = _job_fk(unique=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")  # short topic list (FR-17)
    description: Mapped[str] = mapped_column(Text, default="")
    # [{"start": 0.0, "title": "..."}, ...]
    chapters: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    thumbnail_path: Mapped[str | None] = mapped_column(String(1024))
    # Model, prompt names, attempts, fallback used (FR-19).
    generator: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    job: Mapped[Job] = relationship(back_populates="video_metadata")


class ConsentCheck(Base):
    """One consent check of a group before upload (PRD ConsentCheck; FR-20 ... FR-22)."""

    __tablename__ = "consent_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = _job_fk()
    checked_at: Mapped[datetime] = mapped_column(default=utcnow)
    provider: Mapped[str] = mapped_column(String(40))  # "file", "http", ...
    group_ref: Mapped[str | None] = mapped_column(String(200))  # Core Engine group id
    # [{"id": "s-17", "status": "consented"}, ...] as reported by the provider.
    students: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    result: Mapped[ConsentResult] = mapped_column(enum_type(ConsentResult))
    message: Mapped[str | None] = mapped_column(Text)
    released_by: Mapped[str | None] = mapped_column(String(200))
    released_at: Mapped[datetime | None]
    release_note: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="consent_checks")

    @property
    def students_checked(self) -> int:
        return len(self.students)


# --- Publishing ------------------------------------------------------------------------------


class PublicationTarget(Base):
    """Publication of a job's video to one target, retried on its own (FR-23 ... FR-29).

    ``external_id``: YouTube video id, Slack message ts, Classroom post id, Library entry id.
    ``url``: watch URL, Slack message link, Classroom link, Library link.
    ``details``: the rest, e.g. ``playlist_id`` for YouTube.
    """

    __tablename__ = "publication_targets"
    __table_args__ = (
        UniqueConstraint("job_id", "target"),
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    target: Mapped[TargetKind] = mapped_column(enum_type(TargetKind))
    status: Mapped[PublicationStatus] = mapped_column(
        enum_type(PublicationStatus), default=PublicationStatus.PENDING
    )
    dry_run: Mapped[bool] = mapped_column(default=False)  # D-016
    external_id: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(1024))
    details: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    job: Mapped[Job] = relationship(back_populates="publications")


class LibraryEntry(Base):
    """Interim Academy Library catalogue entry inside Reel (D-008; FR-28)."""

    __tablename__ = "library_entries"
    __table_args__ = (_NO_ID_REUSE,)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = _job_fk(unique=True)
    title: Mapped[str] = mapped_column(String(200))
    video_url: Mapped[str] = mapped_column(String(1024))
    group_name: Mapped[str] = mapped_column(String(200))
    lesson_number: Mapped[int | None]
    recording_date: Mapped[date | None]
    chapters: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    job: Mapped[Job] = relationship(back_populates="library_entry")


# --- Staff -----------------------------------------------------------------------------------


class StaffUser(Base):
    """Dashboard login (T-802). Usernames are stored in canonical form.

    T-1013: an ``admin`` sees every recording and manages the accounts; a ``user`` (who
    registered in the dashboard) sees only their own uploads. A user's uploads are only
    processed, unless the admin allowed them to publish (``can_publish``)."""

    __tablename__ = "staff_users"
    __table_args__ = (Index("ix_staff_users_email", "email", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(default=True)
    role: Mapped[str] = mapped_column(String(16), default=ROLE_USER, server_default=ROLE_USER)
    can_publish: Mapped[bool] = mapped_column(default=False, server_default=false())
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    last_login_at: Mapped[datetime | None]

    def __repr__(self) -> str:  # never shows the password hash
        return f"<StaffUser id={self.id} username={self.username!r}>"

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def publishes(self) -> bool:
        """Whether this account's uploads are published, not only processed."""
        return self.is_admin or self.can_publish

    @validates("username")
    def _normalize(self, _key: str, value: str) -> str:
        return normalize_username(value)


# --- Bookkeeping -----------------------------------------------------------------------------


class SchemaVersion(Base):
    """One row per applied schema version; the highest is the current one."""

    __tablename__ = "schema_versions"

    version: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    applied_at: Mapped[datetime] = mapped_column(default=utcnow)
    app_version: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")


class AppMeta(Base):
    """Key/value facts about this database, e.g. its random ``instance_id``."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
