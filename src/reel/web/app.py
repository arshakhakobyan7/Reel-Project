"""The staff dashboard's web app: login, logout, sessions, CSRF and the start page (T-802;
FR-30, NFR-5, D-049).

T-1013 (D-065): an admin sees every recording, the metrics and the accounts; a user who
registered sees and changes only their own uploads, and cannot run the admin's actions
(accept a hold for another reason, re-run a stage).

Server-rendered Jinja2 pages with plain CSS, no JavaScript. Every page except the login
page and the stylesheet needs a signed-in, active staff account; anonymous visitors are
sent to the login page. Every POST is checked against the session's CSRF token.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from reel import __version__, metrics
from reel.config.loader import LoadedConfig
from reel.db import (
    ROLE_ADMIN,
    ROLE_USER,
    Database,
    Job,
    Recording,
    StaffUser,
    normalize_username,
    utcnow,
)
from reel.db.enums import JobStatus, Stage
from reel.queue.jobs import InvalidTransition, JobNotFound, JobQueue
from reel.retention import on_youtube
from reel.stages.chapters import SPEECH_UNCLEAR, set_staff_chapters, video_duration
from reel.web import accounts, api, detail, texts, uploads, views
from reel.web.forms import ensure_group, parse_chapters, parse_group, parse_lesson
from reel.web.security import (
    LoginThrottle,
    Session,
    SessionSigner,
    csrf_matches,
    password_fingerprint,
    verify_password,
)

log = logging.getLogger("reel.web")

COOKIE = "reel_session"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
HERE = Path(__file__).parent
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PERIODS = ((7, "7 օր"), (30, "30 օր"), (90, "90 օր"), (0, "Ամբողջ ժամանակը"))
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; "  # only Reel's own upload.js (T-807)
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


@dataclass(frozen=True)
class Staff:
    """The signed-in staff member (no password data)."""

    id: int
    username: str
    display_name: str
    role: str = ROLE_USER
    can_publish: bool = False

    @property
    def name(self) -> str:
        return self.display_name or self.username

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def publishes(self) -> bool:
        """Whether this account's uploads are published, not only processed (T-1013)."""
        return self.is_admin or self.can_publish

    @classmethod
    def of(cls, user: StaffUser) -> Staff:
        return cls(user.id, user.username, user.display_name or "", user.role, user.can_publish)


@dataclass
class WebState:
    db: Database
    config: Callable[[], LoadedConfig]
    signer: SessionSigner
    throttle: LoginThrottle
    secure_cookies: bool
    templates: Jinja2Templates
    # New accounts per client address (T-1013): a few an hour, so nobody fills the database.
    registrations: LoginThrottle = field(
        default_factory=lambda: LoginThrottle(max_failures=5, window_seconds=3600)
    )


class NotSignedIn(Exception):
    def __init__(self, path: str) -> None:
        self.path = path


class CsrfFailed(Exception):
    pass


def safe_next(value: str | None) -> str:
    """A local path to go to after login; anything else (another site) becomes "/"."""
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")):
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or any(ord(c) < 32 for c in value):
        return "/"
    return value


def _state(request: Request) -> WebState:
    return request.app.state.reel


def _load_staff(db: Database, session: Session) -> Staff | None:
    with db.transaction() as s:
        user = s.get(StaffUser, session.user_id)
        if (
            user is None
            or not user.is_active
            or password_fingerprint(user.password_hash) != session.fingerprint
        ):
            return None
        return Staff.of(user)


def _check_login(db: Database, username: str, password: str) -> tuple[Staff, str] | None:
    """The account and its password fingerprint, or None. Takes the same time either way."""
    try:
        canonical = normalize_username(username)
    except ValueError:
        canonical = None
    with db.transaction() as s:
        user = (
            s.scalar(select(StaffUser).where(StaffUser.username == canonical))
            if canonical
            else None
        )
        stored = user.password_hash if user is not None else None
        if not verify_password(password, stored) or user is None or not user.is_active:
            return None
        return Staff.of(user), password_fingerprint(stored)


def _mark_login(db: Database, user_id: int) -> None:
    with db.transaction(immediate=True) as s:
        s.get(StaffUser, user_id).last_login_at = utcnow()


def staff(request: Request) -> Staff:
    """Dependency: the signed-in staff member; anonymous visitors go to the login page."""
    user = request.state.user
    if user is None:
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise NotSignedIn(path)
    return user


def admin(request: Request) -> Staff:
    """Dependency: the signed-in admin (T-1013); other accounts get "only for the admin"."""
    user = staff(request)
    if not user.is_admin:
        raise StarletteHTTPException(403, texts.ADMIN_ONLY)
    return user


def reachable(request: Request, job_id: int, user: Staff) -> None:
    """A user reaches only their own uploads; for anything else it does not exist (404)."""
    if user.is_admin:
        return
    with _state(request).db.transaction() as s:
        job = s.get(Job, job_id)
        owner = job.recording.uploaded_by_id if job is not None else None
    if owner is None or owner != user.id:
        raise StarletteHTTPException(404)


async def csrf_guard(request: Request) -> None:
    """Dependency for every route: an unsafe request needs the session's CSRF token."""
    if request.method not in UNSAFE:
        return
    token = request.headers.get(CSRF_HEADER)
    if token is None:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            value = form.get(CSRF_FIELD)
            token = value if isinstance(value, str) else None
    if not csrf_matches(request.state.session, token):
        raise CsrfFailed()


def _unclear(job: Job) -> bool:
    """The job waits for staff to decide about a video whose speech was not clear (T-1012)."""
    return job.status is JobStatus.ON_HOLD and job.hold_reason == SPEECH_UNCLEAR


def render(request: Request, name: str, context: dict[str, Any], status: int = 200) -> Response:
    state = _state(request)
    values = {
        "user": request.state.user,
        "csrf": request.state.session.csrf,
        "version": __version__,
        "texts": texts,
        "registration": state.config().settings.web.registration,  # T-1013
        **context,
    }
    return state.templates.TemplateResponse(request, name, values, status_code=status)


def create_app(
    db: Database,
    config: Callable[[], LoadedConfig],
    secret: str,
    *,
    clock: Callable[[], float] = time.time,
    throttle: LoginThrottle | None = None,
) -> FastAPI:
    cfg = config()
    web = cfg.settings.web
    signer = SessionSigner(secret, lifetime_seconds=web.session_hours * 3600, clock=clock)
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals["stage_label"] = lambda stage: texts.STAGE_LABELS.get(stage, stage)
    templates.env.globals["status_label"] = lambda status: texts.STATUS_LABELS.get(status, status)
    templates.env.globals["clock"] = views.clock_text
    app = FastAPI(
        title="Reel", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
        dependencies=[Depends(csrf_guard)],
    )  # fmt: skip
    app.state.reel = WebState(
        db, config, signer, throttle or LoginThrottle(), web.base_url.startswith("https://"),
        templates,
    )  # fmt: skip
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def sessions(request: Request, call_next: Callable) -> Response:
        state = _state(request)
        if request.url.path.startswith(api.PREFIX + "/"):  # tokens, no sessions or cookies
            request.state.session = request.state.user = request.state.replace = None
            response = await call_next(request)
            for name, value in SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
            response.headers["Cache-Control"] = "no-store"
            return response
        session = state.signer.loads(request.cookies.get(COOKIE))
        fresh = session is None
        if session is None:
            session = state.signer.new()
        user = None
        if session.user_id is not None:
            user = await run_in_threadpool(_load_staff, state.db, session)
            if user is None:  # disabled, deleted, or the password changed: start again
                session, fresh = state.signer.new(), True
        request.state.session, request.state.user, request.state.replace = session, user, None
        response = await call_next(request)
        final = request.state.replace or (session if fresh else None)
        if final is not None:
            response.set_cookie(
                COOKIE, state.signer.dumps(final),
                max_age=max(1, int(final.expires - state.signer.clock())), path="/",
                secure=state.secure_cookies, httponly=True, samesite="lax",
            )  # fmt: skip
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(NotSignedIn)
    async def _to_login(request: Request, exc: NotSignedIn) -> Response:
        target = "/login" if exc.path in {"", "/"} else f"/login?next={quote(exc.path, safe='')}"
        return RedirectResponse(target, status_code=303)

    @app.exception_handler(CsrfFailed)
    async def _csrf(request: Request, exc: CsrfFailed) -> Response:
        log.warning("CSRF check failed for %s %s", request.method, request.url.path)
        return render(request, "error.html", {"message": texts.CSRF_FAILED}, status=403)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> Response:
        if request.url.path.startswith(api.PREFIX + "/"):
            return JSONResponse({"error": str(exc.detail).lower()}, status_code=exc.status_code)
        message = texts.NOT_FOUND if exc.status_code == 404 else str(exc.detail)
        return render(request, "error.html", {"message": message}, status=exc.status_code)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/") -> Response:
        if request.state.user is not None:
            return RedirectResponse(safe_next(next), status_code=303)
        return render(request, "login.html", {"next": safe_next(next), "error": None})

    @app.post("/login", response_class=HTMLResponse)
    def login(
        request: Request,
        username: Annotated[str, Form()] = "",
        password: Annotated[str, Form()] = "",
        next: Annotated[str, Form()] = "/",
    ) -> Response:
        state = _state(request)
        client = request.client.host if request.client else "unknown"
        keys = (f"user:{username.strip().casefold()}", f"client:{client}")
        target = safe_next(next)
        if state.throttle.blocked(*keys):
            log.warning("login refused (too many failures) for client %s", client)
            context = {"next": target, "error": texts.LOGIN_THROTTLED, "username": username}
            return render(request, "login.html", context, status=429)
        found = _check_login(state.db, username, password)
        if found is None:
            state.throttle.failed(*keys)
            log.info("failed login from client %s", client)
            context = {"next": target, "error": texts.LOGIN_FAILED, "username": username}
            return render(request, "login.html", context, status=401)
        user, fingerprint = found
        state.throttle.succeeded(*keys)
        _mark_login(state.db, user.id)
        log.info("staff %s signed in", user.username)
        request.state.replace = state.signer.new(user_id=user.id, fingerprint=fingerprint)
        return RedirectResponse(target, status_code=303)

    @app.post("/logout")
    def logout(request: Request) -> Response:
        user = request.state.user
        if user is not None:
            log.info("staff %s signed out", user.username)
        request.state.replace = _state(request).signer.new()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def recordings(
        request: Request,
        user: Annotated[Staff, Depends(staff)],
        status: str = "",
        group: str = "",
        page: int = 1,
    ) -> Response:
        """The recordings list (T-803): filters by status and group, newest first."""
        state = _state(request)
        cfg = state.config()
        chosen = JobStatus(status) if status in set(JobStatus) else None
        with state.db.transaction() as s:
            listing = views.recordings_page(
                s, ZoneInfo(cfg.settings.schedule.timezone), status=chosen,
                group=group.strip() or None, page=page,
                configured_groups=tuple(cfg.groups.groups),
                owner=None if user.is_admin else user.id,
            )  # fmt: skip
        context = {
            "listing": listing,
            "status": chosen.value if chosen else "",
            "group": group.strip(),
            "no_group": views.NO_GROUP,
            "refresh": views.REFRESH_SECONDS,
        }
        return render(request, "recordings.html", context)

    def detail_page(
        request: Request, job_id: int, *, message: str | None = None, error: str | None = None,
        status: int = 200, chapters_text: str = "",
    ) -> Response:  # fmt: skip
        state = _state(request)
        cfg = state.config()
        with state.db.transaction() as s:
            found = detail.load_detail(
                s, job_id, ZoneInfo(cfg.settings.schedule.timezone),
                cutoff=cfg.settings.schedule.same_evening_cutoff,
                budget_minutes=cfg.settings.budget.max_processing_minutes,
            )  # fmt: skip
            on_tube = found is not None and on_youtube(s.get(Job, job_id))
        if found is None:
            raise StarletteHTTPException(404)
        context = {
            "d": found,
            "message": message,
            "error": error,
            "groups": sorted(cfg.groups.groups, key=str.casefold),
            "video": detail.video_path(job_id, cfg.paths.data_dir) is not None,
            "on_youtube": on_tube,
            "chapters_text": chapters_text,
            "speech_unclear": SPEECH_UNCLEAR,
            "chapter_rules": (
                cfg.settings.metadata.min_chapters,
                cfg.settings.metadata.min_chapter_seconds,
            ),
        }
        return render(request, "recording.html", context, status=status)

    def act(request: Request, job_id: int, user: Staff, done: str, operation: Callable) -> Response:
        """Run a staff action on the job queue (audited there with the staff name)."""
        try:
            operation(JobQueue(_state(request).db), user.username)
        except JobNotFound:
            raise StarletteHTTPException(404) from None
        except (InvalidTransition, ValueError) as exc:
            log.info("staff %s: %s on job %s refused: %s", user.username, done, job_id, exc)
            error = f"{texts.ACTION_FAILED}։ {exc}"
            return detail_page(request, job_id, error=error, status=409)
        log.info("staff %s: %s on job %s", user.username, done, job_id)
        return RedirectResponse(f"/recordings/{job_id}?done={done}", status_code=303)

    @app.get("/recordings/{job_id}", response_class=HTMLResponse)
    def recording(
        request: Request, job_id: int, user: Annotated[Staff, Depends(staff)], done: str = ""
    ) -> Response:
        """The detail page of one recording (T-804)."""
        reachable(request, job_id, user)
        return detail_page(request, job_id, message=texts.DONE_MESSAGES.get(done))

    @app.get("/recordings/{job_id}/video")
    def video(
        request: Request, job_id: int, user: Annotated[Staff, Depends(staff)], download: str = ""
    ) -> Response:
        """The cleaned video, for the page's player (with Range requests) or as a download."""
        reachable(request, job_id, user)
        state = _state(request)
        with state.db.transaction() as s:
            job = s.get(Job, job_id)
            path = detail.video_path(job_id, state.config().paths.data_dir) if job else None
            name = detail.video_file_name(job) if job else ""
        if path is None:
            raise StarletteHTTPException(404)
        if download:
            return FileResponse(path, media_type="video/mp4", filename=name)
        return FileResponse(path, media_type="video/mp4")

    @app.get("/recordings/{job_id}/thumbnail")
    def thumbnail(
        request: Request, job_id: int, user: Annotated[Staff, Depends(staff)]
    ) -> Response:
        reachable(request, job_id, user)
        state = _state(request)
        with state.db.transaction() as s:
            job = s.get(Job, job_id)
            path = detail.thumbnail_path(job, state.config().paths.data_dir) if job else None
        if path is None:
            raise StarletteHTTPException(404)
        media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return FileResponse(path, media_type=media)

    Note = Annotated[str, Form(max_length=1000)]

    @app.post("/recordings/{job_id}/release")
    def release(
        request: Request, job_id: int, user: Annotated[Staff, Depends(admin)],
        advance: Annotated[str, Form()] = "0", note: Note = "",
    ) -> Response:  # fmt: skip
        forward = advance == "1"
        return act(
            request, job_id, user, "accepted" if forward else "released",
            lambda queue, by: queue.release(job_id, by, advance=forward, note=note.strip()),
        )  # fmt: skip

    @app.post("/recordings/{job_id}/publish")
    def publish(
        request: Request, job_id: int, user: Annotated[Staff, Depends(staff)], note: Note = ""
    ) -> Response:
        """The speech was not clear (T-1012): publish the video as it is, without chapters."""
        reachable(request, job_id, user)
        with _state(request).db.transaction() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise StarletteHTTPException(404)
            waiting, published = _unclear(job), job.recording.publish
        if not waiting:
            log.info("staff %s: publish on job %s refused: not waiting", user.username, job_id)
            return detail_page(request, job_id, error=texts.ACTION_FAILED, status=409)
        text = ("published" if published else "finished") + " without chapters"
        text += f": {note.strip()}" if note.strip() else ""
        return act(
            request, job_id, user, "published" if published else "finished",
            lambda queue, by: queue.release(job_id, by, advance=True, note=text),
        )  # fmt: skip

    @app.post("/recordings/{job_id}/chapters")
    def staff_chapters(
        request: Request, job_id: int, user: Annotated[Staff, Depends(staff)],
        chapters: Annotated[str, Form(max_length=20000)] = "",
    ) -> Response:  # fmt: skip
        """The speech was not clear (T-1012): staff set the chapters, then the video goes on to
        be published with them. A mistake in the list shows the page again with the text."""
        reachable(request, job_id, user)
        state = _state(request)
        cfg = state.config()
        try:
            with state.db.transaction(immediate=True) as s:
                job = s.get(Job, job_id)
                if job is None:
                    raise StarletteHTTPException(404)
                if not _unclear(job) or job.video_metadata is None:
                    raise InvalidTransition("the job is not waiting for chapters")
                items = parse_chapters(chapters, video_duration(job.video_metadata),
                                       cfg.settings.metadata)  # fmt: skip
                listed = set_staff_chapters(job, items, cfg, user.username)
                published = job.recording.publish
        except InvalidTransition as exc:
            log.info("staff %s: chapters on job %s refused: %s", user.username, job_id, exc)
            return detail_page(request, job_id, error=texts.ACTION_FAILED, status=409)
        except ValueError as exc:
            return detail_page(request, job_id, error=str(exc), status=422, chapters_text=chapters)
        verb = "published" if published else "finished"
        text = f"{verb} with {len(listed)} chapters set by staff"
        return act(
            request, job_id, user, "chapters" if published else "chapters_finished",
            lambda queue, by: queue.release(job_id, by, advance=True, note=text),
        )  # fmt: skip

    @app.post("/recordings/{job_id}/cancel")
    def cancel(
        request: Request, job_id: int, user: Annotated[Staff, Depends(staff)], note: Note = ""
    ) -> Response:
        reachable(request, job_id, user)
        return act(request, job_id, user, "cancelled",
                   lambda queue, by: queue.cancel(job_id, by, note=note.strip()))  # fmt: skip

    @app.post("/recordings/{job_id}/identify")
    def identify(
        request: Request, job_id: int, user: Annotated[Staff, Depends(staff)],
        group: Annotated[str, Form()] = "", lesson: Annotated[str, Form()] = "",
        recorded: Annotated[str, Form()] = "", note: Note = "",
    ) -> Response:  # fmt: skip
        reachable(request, job_id, user)

        def operation(queue: JobQueue, by: str) -> None:
            number = parse_lesson(lesson)
            day = date.fromisoformat(recorded) if recorded.strip() else None
            cfg = _state(request).config()
            # A new group goes into groups.toml only for an account that publishes (T-1013).
            if user.publishes:
                name = ensure_group(group, cfg, by)
            else:
                name = parse_group(group, cfg.groups)
            queue.identify(job_id, by, group=name, lesson=number, recording_date=day,
                           note=note.strip())  # fmt: skip

        return act(request, job_id, user, "identified", operation)

    @app.post("/recordings/{job_id}/rerun")
    def rerun(
        request: Request, job_id: int, user: Annotated[Staff, Depends(admin)],
        stage: Annotated[str, Form()] = "", note: Note = "",
    ) -> Response:  # fmt: skip
        def operation(queue: JobQueue, by: str) -> None:
            queue.rerun(job_id, by, Stage(stage), note=note.strip())

        return act(request, job_id, user, "rerun", operation)

    @app.post("/recordings/{job_id}/retry")
    def retry(request: Request, job_id: int, user: Annotated[Staff, Depends(staff)]) -> Response:
        reachable(request, job_id, user)
        return act(request, job_id, user, "retried", lambda queue, by: queue.retry_now(job_id, by))

    def summary(request: Request, days: int) -> metrics.Summary:
        state = _state(request)
        cfg = state.config()
        with state.db.transaction() as s:
            return metrics.summarize(
                s, days=max(days, 0), zone=ZoneInfo(cfg.settings.schedule.timezone),
                cutoff=cfg.settings.schedule.same_evening_cutoff,
                budget_minutes=cfg.settings.budget.max_processing_minutes,
            )  # fmt: skip

    @app.get("/metrics", response_class=HTMLResponse)
    def metrics_page(
        request: Request, user: Annotated[Staff, Depends(admin)], days: int = 30
    ) -> Response:
        """The PRD success metrics and the compute "cost" (T-805)."""
        found = summary(request, days)
        recent = sorted(found.jobs_detail, key=lambda job: -job.job_id)[: views.PAGE_SIZE]
        with _state(request).db.transaction() as s:
            titles = {
                job.id: views.recording_title(recording)
                for job, recording in s.execute(
                    select(Job, Recording)
                    .join(Recording, Job.recording_id == Recording.id)
                    .where(Job.id.in_([job.job_id for job in recent]))
                )
            }
        context = {"m": found, "days": found.days, "recent": recent, "titles": titles,
                   "periods": PERIODS}  # fmt: skip
        return render(request, "metrics.html", context)

    @app.get("/metrics.json")
    def metrics_json(
        request: Request, user: Annotated[Staff, Depends(admin)], days: int = 30
    ) -> Response:
        return JSONResponse(summary(request, days).to_dict())

    uploads.install(app, staff=staff, render=render, state=_state)
    accounts.install(app, staff=staff, admin=admin, render=render, state=_state, account=Staff.of)
    api.install(app, state=_state)

    return app
