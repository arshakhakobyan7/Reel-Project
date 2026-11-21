"""Upload routes of the dashboard (T-807; D-035, D-051).

``GET /upload`` is the form. The browser script (``static/upload.js``) talks to a small JSON
API, sending the CSRF token in the ``X-CSRF-Token`` header:

- ``POST /uploads``: start (group, lesson, date, note, the files' names, sizes and times);
- ``GET /uploads/<id>``: how much of each file has arrived (to resume);
- ``PUT /uploads/<id>/files/<n>?offset=<bytes>``: the next piece, streamed to the file;
- ``POST /uploads/<id>/finish``: create the recording and its job;
- ``DELETE /uploads/<id>``: give up.

T-1013: an upload belongs to the account that started it; only it (or an admin) can go on
with it. The recording is published only when the account may publish; otherwise it is
only processed, and a group it names is not added to groups.toml.
"""

import json
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from reel.config.loader import LoadedConfig
from reel.ingest.upload import Lesson, UploadError, UploadStore
from reel.web.forms import ensure_group, parse_group, parse_lesson

NOT_FOUND = "Այդպիսի վերբեռնում չկա։"

CHUNK_BYTES = 8 * 1024 * 1024  # what the browser sends per request


def _error(exc: UploadError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def _lesson(body: dict[str, Any], cfg: LoadedConfig, by: str, publishes: bool) -> Lesson:
    lesson, typed = body.get("lesson"), str(body.get("group") or "")
    try:
        parse_group(typed, cfg.groups)
        number = parse_lesson(lesson if isinstance(lesson, str | int | None) else str(lesson))
    except ValueError as exc:
        raise UploadError(str(exc)) from None
    raw_date = body.get("date") or ""
    try:
        day = date.fromisoformat(raw_date) if raw_date else None
    except TypeError, ValueError:
        raise UploadError("Ամսաթիվը սխալ է։") from None
    note = str(body.get("note") or "")[:1000]
    # A new group goes into groups.toml only now, and only for an account that publishes.
    group = ensure_group(typed, cfg, by) if publishes else parse_group(typed, cfg.groups)
    return Lesson(group, number, day, note)


def install(app: FastAPI, *, staff: Callable, render: Callable, state: Callable) -> None:
    """Add the upload routes. ``staff`` is the sign-in dependency, ``render`` renders a page,
    ``state(request)`` gives the app state (database and configuration)."""

    def store(request: Request) -> UploadStore:
        cfg = state(request).config()
        return UploadStore(
            cfg.paths.data_dir,
            extensions=cfg.settings.ingest.extensions,
            max_file_bytes=int(cfg.settings.web.upload_max_gb * 1024**3),
        )

    def own(request: Request, upload_id: str, user: Any) -> UploadStore:
        """The store, when the upload is this account's own (an admin may go on with any)."""
        uploads = store(request)
        if not user.is_admin and uploads.owner(upload_id) != user.username:
            raise UploadError(NOT_FOUND, 404)
        return uploads

    async def json_body(request: Request) -> dict[str, Any]:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            raise UploadError("Սխալ հարցում։") from None
        if not isinstance(body, dict):
            raise UploadError("Սխալ հարցում։")
        return body

    @app.get("/upload", response_class=HTMLResponse)
    def upload_page(request: Request, user: Annotated[Any, Depends(staff)]) -> Response:
        cfg = state(request).config()
        context = {
            "groups": sorted(cfg.groups.groups, key=str.casefold) if user.publishes else [],
            "publishes": user.publishes,
            "keep_days": cfg.settings.retention.processed_only_days,  # T-1014
            "extensions": ",".join(cfg.settings.ingest.extensions),
            "max_gb": cfg.settings.web.upload_max_gb,
            "chunk_bytes": CHUNK_BYTES,
        }
        return render(request, "upload.html", context)

    @app.post("/uploads")
    async def create(request: Request, user: Annotated[Any, Depends(staff)]) -> Response:
        try:
            body = await json_body(request)
            lesson = _lesson(body, state(request).config(), user.username, user.publishes)
            result = await run_in_threadpool(
                store(request).create, user.username, lesson, body.get("files")
            )
        except UploadError as exc:
            return _error(exc)
        return JSONResponse(result, status_code=201)

    @app.get("/uploads/{upload_id}")
    def status(request: Request, upload_id: str, user: Annotated[Any, Depends(staff)]) -> Response:
        try:
            return JSONResponse(own(request, upload_id, user).status(upload_id))
        except UploadError as exc:
            return _error(exc)

    @app.put("/uploads/{upload_id}/files/{index}")
    async def piece(
        request: Request,
        upload_id: str,
        index: int,
        offset: int,
        user: Annotated[Any, Depends(staff)],
    ) -> Response:
        try:
            uploads = own(request, upload_id, user)
        except UploadError as exc:
            return _error(exc)
        try:
            with uploads.writer(upload_id, index, offset) as write:
                try:
                    async for data in request.stream():
                        if data:
                            write(data)
                except ClientDisconnect:
                    pass  # what arrived is kept; the browser resumes from there
            return JSONResponse(uploads.status(upload_id)["files"][index])
        except UploadError as exc:
            if exc.status == 409 and exc.args[0].startswith("offset"):
                files = uploads.status(upload_id)["files"]
                return JSONResponse({"error": "offset", **files[index]}, status_code=409)
            return _error(exc)

    @app.post("/uploads/{upload_id}/finish")
    def finish(request: Request, upload_id: str, user: Annotated[Any, Depends(staff)]) -> Response:
        db = state(request).db
        try:
            job_id = own(request, upload_id, user).finish(
                upload_id, lambda: db.transaction(immediate=True), user.username,
                owner_id=user.id, publish=user.publishes,
            )  # fmt: skip
        except UploadError as exc:
            return _error(exc)
        return JSONResponse({"job_id": job_id, "url": f"/recordings/{job_id}"})

    @app.delete("/uploads/{upload_id}")
    def cancel(request: Request, upload_id: str, user: Annotated[Any, Depends(staff)]) -> Response:
        try:
            own(request, upload_id, user).cancel(upload_id)
        except UploadError as exc:
            return _error(exc)
        return Response(status_code=204)
