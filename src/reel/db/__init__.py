"""Reel database: SQLite through SQLAlchemy (T-101, D-027).

Typical use::

    from reel.db import open_database, Job

    db = open_database(cfg.paths.database_file)
    with db.transaction() as session:
        job = session.get(Job, 14)
"""

from reel.db.base import Base, UtcDateTime, json_dumps, utcnow
from reel.db.engine import DEFAULT_BUSY_TIMEOUT, Database, DatabaseError, database_url
from reel.db.enums import (
    PIPELINE,
    STAGE_LABELS,
    ActorKind,
    ConsentResult,
    CutAction,
    EventKind,
    JobStatus,
    PublicationStatus,
    SeenFileState,
    Stage,
    StageRunStatus,
    TargetKind,
    TranscriptKind,
    next_stage,
)
from reel.db.models import (
    AppMeta,
    ConsentCheck,
    CutList,
    Job,
    JobEvent,
    LibraryEntry,
    PublicationTarget,
    Recording,
    SchemaVersion,
    SeenFile,
    StaffUser,
    StageRun,
    Transcript,
    VideoMetadata,
    normalize_username,
    validate_segments,
)
from reel.db.versioning import (
    MIGRATIONS,
    SCHEMA_VERSION,
    DatabaseInfo,
    Migration,
    SchemaState,
    UpgradeResult,
    backup_database,
    init_database,
    inspect_database,
    open_database,
    upgrade_database,
)

__all__ = [
    "DEFAULT_BUSY_TIMEOUT",
    "MIGRATIONS",
    "PIPELINE",
    "SCHEMA_VERSION",
    "STAGE_LABELS",
    "ActorKind",
    "AppMeta",
    "Base",
    "ConsentCheck",
    "ConsentResult",
    "CutAction",
    "CutList",
    "Database",
    "DatabaseError",
    "DatabaseInfo",
    "EventKind",
    "Job",
    "JobEvent",
    "JobStatus",
    "LibraryEntry",
    "Migration",
    "PublicationStatus",
    "PublicationTarget",
    "Recording",
    "SchemaState",
    "SchemaVersion",
    "SeenFile",
    "SeenFileState",
    "Stage",
    "StageRun",
    "StageRunStatus",
    "StaffUser",
    "TargetKind",
    "Transcript",
    "TranscriptKind",
    "UpgradeResult",
    "UtcDateTime",
    "VideoMetadata",
    "backup_database",
    "database_url",
    "init_database",
    "inspect_database",
    "json_dumps",
    "next_stage",
    "normalize_username",
    "open_database",
    "upgrade_database",
    "utcnow",
    "validate_segments",
]
