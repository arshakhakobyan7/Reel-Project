"""Fixed vocabularies stored in the database. The enum values are the stored strings."""

from enum import StrEnum


class Stage(StrEnum):
    """Pipeline stages in processing order (D-012)."""

    IMPORT = "import"
    TRANSCRIBE = "transcribe"
    CLEAN = "clean"
    CHAPTERS = "chapters"
    CONSENT = "consent"
    UPLOAD = "upload"
    POST = "post"

    @property
    def label(self) -> str:
        """Name shown in the dashboard (PRD FR-5)."""
        return STAGE_LABELS[self]


STAGE_LABELS: dict[Stage, str] = {
    Stage.IMPORT: "Imported",
    Stage.TRANSCRIBE: "Transcribed",
    Stage.CLEAN: "Cleaned",
    Stage.CHAPTERS: "Chapters",
    Stage.CONSENT: "Consent",
    Stage.UPLOAD: "Uploaded",
    Stage.POST: "Posting links",
}

PIPELINE: tuple[Stage, ...] = tuple(Stage)


def next_stage(stage: Stage) -> Stage | None:
    """The stage after ``stage``, or None after the last one."""
    index = PIPELINE.index(stage) + 1
    return PIPELINE[index] if index < len(PIPELINE) else None


class EventKind(StrEnum):
    """Kinds of job events written by the job queue (T-102). Other modules add their own
    kinds as plain strings; the column is not limited to these."""

    CREATED = "created"
    STARTED = "started"
    STAGE_COMPLETED = "stage_completed"
    DONE = "done"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    HELD = "held"
    RELEASED = "released"
    CANCELLED = "cancelled"
    RERUN = "rerun"
    RETRY_NOW = "retry_now"
    LEASE_EXPIRED = "lease_expired"
    INTERRUPTED = "interrupted"
    IDENTIFIED = "identified"  # staff set the group, lesson or date (T-804)


class JobStatus(StrEnum):
    """Where a job is in its life cycle (ARCHITECTURE.md §4). Transitions come in T-102."""

    QUEUED = "queued"
    RUNNING = "running"
    ON_HOLD = "on_hold"
    FAILED = "failed"
    DONE = "done"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


class StageRunStatus(StrEnum):
    """Outcome of one attempt of one stage."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    HELD = "held"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # the worker stopped or crashed (lease expired)


class ActorKind(StrEnum):
    """Who caused a job event."""

    SYSTEM = "system"
    WORKER = "worker"
    STAFF = "staff"


class TranscriptKind(StrEnum):
    """Which timeline the timestamps of a transcript refer to."""

    RAW = "raw"  # the original recording
    CLEAN = "clean"  # the cleaned video (FR-13)


class CutAction(StrEnum):
    KEEP = "keep"
    REMOVE = "remove"


class ConsentResult(StrEnum):
    """Result of a consent check (FR-20 ... FR-22)."""

    PASSED = "passed"  # every student consented
    OPTED_OUT = "opted_out"  # at least one student opted out
    UNKNOWN = "unknown"  # a status is missing; treated as opted out (D-019)
    UNREACHABLE = "unreachable"  # the consent source could not be reached


class TargetKind(StrEnum):
    """Places a video is published to (FR-23 ... FR-29)."""

    YOUTUBE = "youtube"
    SLACK = "slack"
    CLASSROOM = "classroom"
    LIBRARY = "library"


class PublicationStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PUBLISHED = "published"
    FAILED = "failed"
    SKIPPED = "skipped"  # not enabled or not configured for the group


class SeenFileState(StrEnum):
    """Folder watcher state of a file (T-202)."""

    WAITING = "waiting"  # seen; waiting until it stops changing (FR-2)
    QUEUED = "queued"  # stable; a recording and its job were created
    GONE = "gone"  # deleted before it became stable
    IGNORED = "ignored"  # not processed; the note says why
    ERROR = "error"
