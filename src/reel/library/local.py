"""Transaction-aware upsert into the interim LibraryEntry table (T-705)."""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from reel.db import Database, LibraryEntry, utcnow
from reel.library.port import (
    LibraryItem,
    LibraryPublisher,
    LibraryResult,
    PermanentLibraryError,
    RetryableLibraryError,
    _job_id,
)

log = logging.getLogger("reel.library")


def upsert_library_entry(session: Session, job_id: int, item: LibraryItem) -> LibraryResult:
    """Flush one upsert; never commit, rollback, close or create another session.

    INSERT DO NOTHING obtains the write lock before an existing row is read. Its unique
    job_id constraint arbitrates competing creates. The caller owns the transaction.
    """
    _job_id(job_id)
    payload = item.to_dict()
    values = {
        "title": item.title,
        "video_url": item.video_url,
        "group_name": item.group,
        "lesson_number": item.lesson_number,
        "recording_date": item.recording_date,
        "chapters": payload["chapters"],
    }
    now = utcnow()
    try:
        statement = (
            insert(LibraryEntry)
            .values(job_id=job_id, **values, created_at=now, updated_at=now)
            .on_conflict_do_nothing(index_elements=[LibraryEntry.job_id])
            .returning(LibraryEntry.id)
        )
        entry_id = session.execute(statement).scalar_one_or_none()
        created = entry_id is not None
        if not created:
            entry = session.scalar(
                select(LibraryEntry)
                .where(LibraryEntry.job_id == job_id)
                .execution_options(populate_existing=True)
            )
            for name, value in values.items():
                setattr(entry, name, value)
            entry.updated_at = max(now, entry.updated_at + timedelta(microseconds=1))
            entry_id = entry.id
        session.flush()
    except IntegrityError:
        raise PermanentLibraryError("local_constraint_or_missing_job") from None
    except OperationalError:
        raise RetryableLibraryError("local_database_unavailable") from None
    return LibraryResult(str(entry_id), None, created, payload=payload)


class LocalLibraryPublisher(LibraryPublisher):
    """Open a new locked transaction; use upsert_library_entry inside an existing one."""

    name = "local"

    def __init__(self, db: Database, *, dry_run: bool = False) -> None:
        self.db = db
        self._dry_run = dry_run

    def publish(self, job_id: int, item: LibraryItem) -> LibraryResult:
        _job_id(job_id)
        if self._dry_run:
            log.info("Dry-run local library publication prepared for job %s", job_id)
            return LibraryResult(None, None, False, dry_run=True, payload=item.to_dict())
        try:
            with self.db.transaction(immediate=True) as session:
                return upsert_library_entry(session, job_id, item)
        except OperationalError:
            raise RetryableLibraryError("local_database_unavailable") from None
