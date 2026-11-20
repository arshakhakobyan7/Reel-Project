"""Read-only integration API for the other teams' products (T-806; FR-28, D-008, D-053).

``/api/v1/recordings``, ``/api/v1/recordings/{id}`` and ``/api/v1/library`` answer JSON to a
request with ``Authorization: Bearer <token>``, where the token is one of
``REEL_API_TOKENS`` (comma-separated, each at least 24 characters). Without configured tokens
the API is off (503). The API never shows transcripts, students or files, and it shows only
real publication links (not dry-run previews). The contract is
``docs/integrations/reel-api.md``.
"""

import hmac
import logging
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from reel.db import (
    ConsentCheck,
    CutList,
    Job,
    LibraryEntry,
    Recording,
)
from reel.db.enums import JobStatus

log = logging.getLogger("reel.web.api")

PREFIX = "/api/v1"
MIN_TOKEN = 24
MAX_LIMIT = 200


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def tokens(raw: str | None) -> list[str]:
    """The usable tokens; too short ones are ignored (and logged)."""
    found = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        if len(token) < MIN_TOKEN:
            log.warning("a token in REEL_API_TOKENS is shorter than %d characters: ignored",
                        MIN_TOKEN)  # fmt: skip
            continue
        found.append(token)
    return found


def check_token(request: Request, allowed: list[str]) -> None:
    if not allowed:
        raise ApiError(503, "the integration API is off: set REEL_API_TOKENS")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    given = token.strip().encode("utf-8", "replace")
    ok = scheme.lower() == "bearer" and any(
        hmac.compare_digest(given, candidate.encode("utf-8")) for candidate in allowed
    )
    if not ok:
        raise ApiError(401, "a valid bearer token is required")


def _time(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _limit(value: str | None, default: int = 50) -> int:
    try:
        number = int(value) if value not in (None, "") else default
    except ValueError:
        raise ApiError(400, "limit must be a number") from None
    return min(max(number, 1), MAX_LIMIT)


def _cursor(value: str | None) -> int:
    try:
        return int(value) if value not in (None, "") else 0
    except ValueError:
        raise ApiError(400, "after must be a recording id") from None


def _since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise ApiError(400, "updated_since must be an ISO 8601 time with a time zone") from None
    if moment.tzinfo is None:
        raise ApiError(400, "updated_since must be an ISO 8601 time with a time zone")
    return moment


def _publications(job: Job) -> dict[str, Any]:
    result = {}
    for row in job.publications:
        live = not row.dry_run
        result[row.target.value] = {
            "status": row.status.value,
            "dry_run": row.dry_run,
            "url": row.url if live else None,
            "published_at": _time(row.published_at) if live else None,
        }
    return result


def _item(job: Job, recording: Recording, final: float | None) -> dict[str, Any]:
    meta = job.video_metadata
    youtube = job.publication_summary()
    youtube_row = next((p for p in job.publications if p.target.value == "youtube"), None)
    return {
        "id": job.id,
        "title": meta.title if meta else None,
        "group": recording.group_name,
        "lesson_number": recording.lesson_number,
        "recording_date": recording.recording_date.isoformat()
        if recording.recording_date
        else None,
        "status": job.status.value,
        "stage": job.stage.value,
        "original_duration_seconds": recording.original_duration_seconds,
        "final_duration_seconds": final,
        "youtube_url": youtube["youtube_url"] if youtube_row and not youtube_row.dry_run else None,
        "flags": list(job.flags or ()),
        "created_at": _time(job.created_at),
        "updated_at": _time(job.updated_at),
        "finished_at": _time(job.finished_at),
    }


def _final(job: Job) -> float | None:
    cut = job.latest_cut_list
    return cut.final_duration_seconds if cut else None


def list_recordings(session: Session, params: dict[str, str]) -> dict[str, Any]:
    limit, after = _limit(params.get("limit")), _cursor(params.get("after"))
    query = (
        select(Job, Recording)
        .join(Recording, Job.recording_id == Recording.id)
        .where(Job.id > after)
        .order_by(Job.id)
    )
    status = params.get("status")
    if status:
        if status not in {s.value for s in JobStatus}:
            raise ApiError(400, f"unknown status {status!r}")
        query = query.where(Job.status == JobStatus(status))
    if params.get("group"):
        query = query.where(Recording.group_name == params["group"])
    since = _since(params.get("updated_since"))
    if since is not None:
        query = query.where(Job.updated_at >= since)
    rows = session.execute(query.limit(limit + 1)).all()
    items = [_item(job, recording, _final(job)) for job, recording in rows[:limit]]
    following = items[-1]["id"] if len(rows) > limit else None
    return {"items": items, "next_after": following}


def one_recording(session: Session, job_id: int) -> dict[str, Any]:
    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "no such recording")
    data = _item(job, job.recording, _final(job))
    meta = job.video_metadata
    data["summary"] = meta.summary if meta else None
    data["description"] = meta.description if meta else None
    data["chapters"] = [
        {"start_seconds": float(c["start"]), "title": c["title"]}
        for c in (meta.chapters if meta else [])
    ]
    data["publications"] = _publications(job)
    check = session.scalars(
        select(ConsentCheck).where(ConsentCheck.job_id == job.id).order_by(ConsentCheck.id.desc())
    ).first()
    data["consent"] = (
        {"result": check.result.value, "checked_at": _time(check.checked_at),
         "released_by_staff": check.released_by is not None}
        if check else None
    )  # fmt: skip
    cuts = session.scalar(select(CutList).where(CutList.job_id == job.id).order_by(
        CutList.version.desc()).limit(1))  # fmt: skip
    data["removed_seconds"] = (
        round(cuts.original_duration_seconds - cuts.final_duration_seconds, 3) if cuts else None
    )
    return data


def list_library(session: Session, params: dict[str, str]) -> dict[str, Any]:
    limit, after = _limit(params.get("limit")), _cursor(params.get("after"))
    query = select(LibraryEntry).where(LibraryEntry.id > after).order_by(LibraryEntry.id)
    if params.get("group"):
        query = query.where(LibraryEntry.group_name == params["group"])
    since = _since(params.get("updated_since"))
    if since is not None:
        query = query.where(LibraryEntry.updated_at >= since)
    rows = session.scalars(query.limit(limit + 1)).all()
    items = [
        {
            "id": entry.id,
            "recording_id": entry.job_id,
            "title": entry.title,
            "video_url": entry.video_url,
            "group": entry.group_name,
            "lesson_number": entry.lesson_number,
            "recording_date": entry.recording_date.isoformat() if entry.recording_date else None,
            "chapters": list(entry.chapters or ()),
            "updated_at": _time(entry.updated_at),
        }
        for entry in rows[:limit]
    ]
    return {"items": items, "next_after": items[-1]["id"] if len(rows) > limit else None}


def install(app: FastAPI, *, state) -> None:
    """Add the API routes; ``state(request)`` gives the database and the configuration."""

    def answer(request: Request, work) -> Response:
        try:
            check_token(request, tokens(state(request).config().secrets.get("REEL_API_TOKENS")))
            with state(request).db.transaction() as session:
                data = work(session)
        except ApiError as exc:
            headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
            return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=headers)
        return JSONResponse(data)

    @app.get(f"{PREFIX}/recordings")
    def recordings(request: Request) -> Response:
        return answer(request, lambda s: list_recordings(s, dict(request.query_params)))

    @app.get(PREFIX + "/recordings/{job_id}")
    def recording(request: Request, job_id: int) -> Response:
        return answer(request, lambda s: one_recording(s, job_id))

    @app.get(f"{PREFIX}/library")
    def library(request: Request) -> Response:
        return answer(request, lambda s: list_library(s, dict(request.query_params)))
