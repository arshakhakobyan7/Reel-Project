"""Accounts in the dashboard (T-1013, D-065): registration, and the admin's account pages.

- ``GET/POST /register``: a new user gives their first and last name, email, an optional
  phone number and a password, and is signed in at once. The email is the username. The
  account's uploads are processed but not published, until the admin allows it.
- ``GET /admin/users``: every account with its recordings (all, in progress, ready).
- ``GET /admin/users/<id>``: one account: its details, recordings and recent actions.
- ``POST /admin/users/<id>/active``: close or open the account. A closed account cannot
  sign in, and its open sessions end at once (the session check reads the account).
- ``POST /admin/users/<id>/publish``: let the account's new uploads be published, or stop it.
"""

import logging
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from reel.db import ROLE_USER, Job, JobEvent, Recording, StaffUser, utcnow
from reel.db.enums import ActorKind, JobStatus
from reel.web import texts
from reel.web.forms import parse_registration
from reel.web.security import hash_password, password_fingerprint
from reel.web.views import clock_text, local_text, recording_title

log = logging.getLogger("reel.web")

RECORDING_LIMIT = 200
ACTION_LIMIT = 50
WORKING = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.ON_HOLD)


def _summary(user: StaffUser, counts: Counter, zone: ZoneInfo) -> dict[str, Any]:
    return {
        "id": user.id,
        "name": user.display_name or user.username,
        "username": user.username,
        "email": user.email or "",
        "phone": user.phone or "",
        "role": texts.ROLE_LABELS.get(user.role, user.role),
        "is_admin": user.is_admin,
        "can_publish": user.can_publish,
        "publishes": user.publishes,
        "active": user.is_active,
        "created": local_text(user.created_at, zone),
        "last_login": local_text(user.last_login_at, zone),
        "total": sum(counts.values()),
        "working": sum(counts[status] for status in WORKING),
        "done": counts[JobStatus.DONE],
        "failed": counts[JobStatus.FAILED],
    }


def _counts(session: Any, owner: int | None = None) -> dict[int, Counter]:
    query = (
        select(Recording.uploaded_by_id, Job.status, func.count())
        .join(Job, Job.recording_id == Recording.id)
        .where(Recording.uploaded_by_id.is_not(None))
        .group_by(Recording.uploaded_by_id, Job.status)
    )
    if owner is not None:
        query = query.where(Recording.uploaded_by_id == owner)
    counts: dict[int, Counter] = defaultdict(Counter)
    for uploader, status, number in session.execute(query):
        counts[uploader][status] += number
    return counts


def install(
    app: FastAPI,
    *,
    staff: Callable,
    admin: Callable,
    render: Callable,
    state: Callable,
    account: Callable[[StaffUser], Any],
) -> None:
    """Add the account routes. ``account(user)`` makes the signed-in staff value."""

    def registration_open(request: Request) -> None:
        if not state(request).config().settings.web.registration:
            raise StarletteHTTPException(404)

    @app.get("/register", response_class=HTMLResponse)
    def register_page(request: Request) -> Response:
        registration_open(request)
        if request.state.user is not None:
            return RedirectResponse("/", status_code=303)
        return render(request, "register.html", {"form": {}, "error": None})

    @app.post("/register", response_class=HTMLResponse)
    def register(
        request: Request,
        first_name: Annotated[str, Form(max_length=200)] = "",
        last_name: Annotated[str, Form(max_length=200)] = "",
        email: Annotated[str, Form(max_length=300)] = "",
        phone: Annotated[str, Form(max_length=100)] = "",
        password: Annotated[str, Form(max_length=1024)] = "",
        password_again: Annotated[str, Form(max_length=1024)] = "",
    ) -> Response:
        registration_open(request)
        if request.state.user is not None:
            return RedirectResponse("/", status_code=303)
        current = state(request)
        form = {"first_name": first_name, "last_name": last_name, "email": email, "phone": phone}

        def again(error: str, status: int) -> Response:
            return render(request, "register.html", {"form": form, "error": error}, status=status)

        client = request.client.host if request.client else "unknown"
        key = f"register:{client}"
        if current.registrations.blocked(key):
            log.warning("registration refused (too many accounts) for client %s", client)
            return again(texts.REGISTER_THROTTLED, 429)
        try:
            new = parse_registration(first_name, last_name, email, phone, password, password_again)
        except ValueError as exc:
            return again(str(exc), 422)
        current.registrations.failed(key)  # every account made counts
        stored = hash_password(new.password)
        with current.db.transaction(immediate=True) as s:
            taken = s.scalar(
                select(StaffUser.id).where(
                    (StaffUser.username == new.email) | (StaffUser.email == new.email)
                )
            )
            if taken is None:
                user = StaffUser(
                    username=new.email, display_name=new.display_name, password_hash=stored,
                    role=ROLE_USER, can_publish=False, first_name=new.first_name,
                    last_name=new.last_name, email=new.email, phone=new.phone,
                    last_login_at=utcnow(),
                )  # fmt: skip
                s.add(user)
                s.flush()
                signed = account(user)
        if taken is not None:
            return again(texts.EMAIL_TAKEN, 409)
        log.info("account %d registered from client %s", signed.id, client)
        request.state.replace = current.signer.new(
            user_id=signed.id, fingerprint=password_fingerprint(stored)
        )
        return RedirectResponse("/", status_code=303)

    Admin = Annotated[Any, Depends(admin)]

    @app.get("/admin/users", response_class=HTMLResponse)
    def users_page(request: Request, user: Admin) -> Response:
        current = state(request)
        zone = ZoneInfo(current.config().settings.schedule.timezone)
        with current.db.transaction() as s:
            counts = _counts(s)
            rows = [
                _summary(each, counts[each.id], zone)
                for each in s.scalars(select(StaffUser).order_by(StaffUser.id.desc()))
            ]
        return render(request, "users.html", {"rows": rows})

    def user_page(
        request: Request, user_id: int, *, message: str | None = None,
        error: str | None = None, status: int = 200,
    ) -> Response:  # fmt: skip
        current = state(request)
        zone = ZoneInfo(current.config().settings.schedule.timezone)
        with current.db.transaction() as s:
            found = s.get(StaffUser, user_id)
            if found is None:
                raise StarletteHTTPException(404)
            profile = _summary(found, _counts(s, found.id)[found.id], zone)
            profile.update(first_name=found.first_name or "", last_name=found.last_name or "")
            recordings = [
                {
                    "job_id": job.id,
                    "title": recording_title(recording),
                    "status": job.status.value,
                    "status_label": texts.STATUS_LABELS[job.status],
                    "created": local_text(job.created_at, zone),
                    "duration": clock_text(recording.original_duration_seconds),
                    "publish": recording.publish,
                }  # fmt: skip
                for job, recording in s.execute(
                    select(Job, Recording)
                    .join(Recording, Job.recording_id == Recording.id)
                    .where(Recording.uploaded_by_id == found.id)
                    .order_by(Job.id.desc())
                    .limit(RECORDING_LIMIT)
                )
            ]
            actions = [
                {
                    "at": local_text(event.at, zone),
                    "job_id": event.job_id,
                    "kind": texts.EVENT_LABELS.get(event.kind, event.kind),
                    "stage": texts.STAGE_LABELS.get(event.stage, "") if event.stage else "",
                    "message": event.message,
                    "note": str((event.data or {}).get("note") or ""),
                }  # fmt: skip
                for event in s.scalars(
                    select(JobEvent)
                    .where(
                        JobEvent.actor_kind == ActorKind.STAFF,
                        JobEvent.actor_name == found.username,
                    )
                    .order_by(JobEvent.id.desc())
                    .limit(ACTION_LIMIT)
                )
            ]
        context = {"u": profile, "recordings": recordings, "actions": actions,
                   "message": message, "error": error}  # fmt: skip
        return render(request, "user.html", context, status=status)

    @app.get("/admin/users/{user_id}", response_class=HTMLResponse)
    def user_detail(request: Request, user_id: int, user: Admin, done: str = "") -> Response:
        return user_page(request, user_id, message=texts.ACCOUNT_MESSAGES.get(done))

    def change(request: Request, user_id: int, user: Any, apply: Callable) -> Response:
        """Change an account; ``apply(account)`` returns the done key or raises ValueError."""
        with state(request).db.transaction(immediate=True) as s:
            found = s.get(StaffUser, user_id)
            if found is None:
                raise StarletteHTTPException(404)
            try:
                done = apply(found)
            except ValueError as exc:
                failed = str(exc)
            else:
                failed = None
        if failed is not None:
            return user_page(request, user_id, error=failed, status=409)
        log.info("admin %s: account %d %s", user.username, user_id, done)
        return RedirectResponse(f"/admin/users/{user_id}?done={done}", status_code=303)

    @app.post("/admin/users/{user_id}/active")
    def set_active(
        request: Request, user_id: int, user: Admin, active: Annotated[str, Form()] = "0"
    ) -> Response:
        def apply(found: StaffUser) -> str:
            if active != "1" and found.id == user.id:
                raise ValueError(texts.CANNOT_CLOSE_SELF)
            found.is_active = active == "1"
            return "opened" if found.is_active else "closed"

        return change(request, user_id, user, apply)

    @app.post("/admin/users/{user_id}/publish")
    def set_publish(
        request: Request, user_id: int, user: Admin, allow: Annotated[str, Form()] = "0"
    ) -> Response:
        def apply(found: StaffUser) -> str:
            if found.is_admin:
                raise ValueError(texts.ADMIN_PUBLISHES)
            found.can_publish = allow == "1"
            return "publish_on" if found.can_publish else "publish_off"

        return change(request, user_id, user, apply)
