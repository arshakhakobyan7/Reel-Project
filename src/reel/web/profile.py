"""The signed-in account's details and password (T-1016, D-068)."""

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException

from reel.db import StaffUser
from reel.web import texts
from reel.web.forms import parse_new_password, parse_profile
from reel.web.security import hash_password, password_fingerprint, verify_password
from reel.web.views import local_text

log = logging.getLogger("reel.web")


def install(app: FastAPI, *, staff: Callable, render: Callable, state: Callable) -> None:
    """Add routes that always use the signed-in account, never a supplied account id."""

    def page(
        request: Request,
        user: Any,
        *,
        form: dict[str, str] | None = None,
        message: str | None = None,
        error: str | None = None,
        status: int = 200,
    ) -> Response:
        current = state(request)
        zone = ZoneInfo(current.config().settings.schedule.timezone)
        with current.db.transaction() as session:
            found = session.get(StaffUser, user.id)
            if found is None or not found.is_active:
                raise StarletteHTTPException(404)
            facts = {
                "username": found.username,
                "role": texts.ROLE_LABELS.get(found.role, found.role),
                "publishes": found.publishes,
                "created": local_text(found.created_at, zone),
                "last_login": local_text(found.last_login_at, zone),
                "email_required": "@" in found.username,
            }
            if form is None:
                form = {
                    "first_name": found.first_name or "",
                    "last_name": found.last_name or "",
                    "email": found.email or "",
                    "phone": found.phone or "",
                }
        return render(
            request,
            "profile.html",
            {"p": facts, "form": form, "message": message, "error": error},
            status=status,
        )

    class ProfileRoute(APIRoute):
        """Keep FastAPI's outer field limits without reflecting invalid secret inputs."""

        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            handler = super().get_route_handler()

            async def checked(request: Request) -> Response:
                try:
                    return await handler(request)
                except RequestValidationError:
                    user = request.state.user
                    if user is None:
                        return RedirectResponse("/login?next=/profile", status_code=303)
                    form = None
                    if request.url.path == "/profile/details":
                        posted = await request.form()
                        form = {
                            key: value if isinstance(value := posted.get(key), str) else ""
                            for key in ("first_name", "last_name", "email", "phone")
                        }
                    return page(
                        request,
                        user,
                        form=form,
                        error=texts.PROFILE_FORM_INVALID,
                        status=422,
                    )

            return checked

    router = APIRouter(route_class=ProfileRoute)
    SignedIn = Annotated[Any, Depends(staff)]

    @router.get("/profile", response_class=HTMLResponse)
    def profile_page(request: Request, user: SignedIn, done: str = "") -> Response:
        return page(request, user, message=texts.PROFILE_MESSAGES.get(done))

    @router.post("/profile/details", response_class=HTMLResponse)
    def details(
        request: Request,
        user: SignedIn,
        first_name: Annotated[str, Form(max_length=200)] = "",
        last_name: Annotated[str, Form(max_length=200)] = "",
        email: Annotated[str, Form(max_length=300)] = "",
        phone: Annotated[str, Form(max_length=100)] = "",
    ) -> Response:
        current = state(request)
        form = {"first_name": first_name, "last_name": last_name, "email": email, "phone": phone}
        try:
            new = parse_profile(
                first_name,
                last_name,
                email,
                phone,
                email_required="@" in user.username,
            )
        except ValueError as exc:
            return page(request, user, form=form, error=str(exc), status=422)
        with current.db.transaction(immediate=True) as session:
            found = session.get(StaffUser, user.id)
            if found is None or not found.is_active:
                raise StarletteHTTPException(404)
            taken = (
                session.scalar(
                    select(StaffUser.id).where(
                        StaffUser.id != found.id,
                        (StaffUser.email == new.email) | (StaffUser.username == new.email),
                    )
                )
                if new.email
                else None
            )
            if taken is None:
                found.first_name, found.last_name = new.first_name, new.last_name
                found.display_name = new.display_name
                found.email, found.phone = new.email or None, new.phone
        if taken is not None:
            return page(request, user, form=form, error=texts.EMAIL_TAKEN, status=409)
        log.info("account %d updated its details", user.id)
        return RedirectResponse("/profile?done=details", status_code=303)

    @router.post("/profile/password", response_class=HTMLResponse)
    def password(
        request: Request,
        user: SignedIn,
        current_password: Annotated[str, Form(max_length=1024)] = "",
        new_password: Annotated[str, Form(max_length=1024)] = "",
        password_again: Annotated[str, Form(max_length=1024)] = "",
    ) -> Response:
        current = state(request)
        client = request.client.host if request.client else "unknown"
        keys = (f"user:{user.username.casefold()}", f"client:{client}")
        if current.throttle.blocked(*keys):
            return page(request, user, error=texts.LOGIN_THROTTLED, status=429)
        error = None
        stored = None
        with current.db.transaction(immediate=True) as session:
            found = session.get(StaffUser, user.id)
            if found is None or not found.is_active:
                raise StarletteHTTPException(404)
            if not verify_password(current_password, found.password_hash):
                current.throttle.failed(*keys)
                error = texts.PROFILE_CURRENT_PASSWORD_WRONG
            else:
                try:
                    new = parse_new_password(
                        new_password,
                        password_again,
                        current=current_password,
                    )
                except ValueError as exc:
                    error = str(exc)
                else:
                    stored = hash_password(new)
                    found.password_hash = stored
        if error is not None:
            return page(request, user, error=error, status=422)
        assert stored is not None
        current.throttle.succeeded(*keys)
        request.state.replace = current.signer.new(
            user_id=user.id,
            fingerprint=password_fingerprint(stored),
        )
        log.info("account %d changed its password", user.id)
        return RedirectResponse("/profile?done=password", status_code=303)

    app.include_router(router)
