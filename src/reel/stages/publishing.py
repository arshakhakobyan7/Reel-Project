"""Stages ``upload`` (YouTube) and ``post`` (Slack, Classroom, Academy Library) (T-706;
FR-23 … FR-29, NFR-2, G-3, D-048).

Upload
- The cleaned video goes to YouTube as unlisted (D-017), with the stored title,
  description, thumbnail and the group's playlist.
- Every state snapshot of the YouTube publisher is saved at once in
  ``publication_targets.details``. After a crash the next attempt resumes the same upload,
  and a retry never makes a second video (NFR-2).
- When the daily YouTube quota is used up, the next attempt waits until the quota resets
  (midnight Pacific Time).

Post
- Each target has its own ``publication_targets`` row (status, attempts, error).
- A failing target does not stop the others. While any target failed, the stage fails
  and the queue retries it with backoff. A retry repeats only the targets that are not
  published yet, and an earlier post is updated, never posted again.
- The job is Done when every target is published or skipped. When the attempts run out,
  the job is Failed, and the rows show what was published.
- A target is skipped when the group has no place for it (no Slack channel or Classroom
  course in ``groups.toml``).
- While YouTube is in dry-run there is no real link, so every post is a dry-run preview
  too, whatever the target's own mode (D-048).
- A dry-run never overwrites a real publication: its preview is kept next to it.

Every write checks that this attempt still holds the job, so an attempt that lost its
lease cannot overwrite the records of a newer one.
"""

import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from reel.auth.google import make_google_tokens
from reel.config.groups import GroupConfig, missing_group_message
from reel.config.loader import LoadedConfig
from reel.config.schema import ClassroomSettings, SlackSettings, YoutubeSettings
from reel.db import AppMeta, Database, Job, PublicationTarget, Stage, StageRun, utcnow
from reel.db.enums import PublicationStatus, StageRunStatus, TargetKind
from reel.library import LibraryError, LibraryItem, LibraryPublisher, make_library_publisher
from reel.metadata.text import format_timestamp
from reel.publish.classroom import ClassroomPost, ClassroomPublisher
from reel.publish.google_api import AccessTokens, GoogleApiError
from reel.publish.slack import SlackError, SlackPost, SlackPublisher
from reel.publish.youtube import YoutubePublisher, YoutubeUpload
from reel.queue.stage import (
    Hold,
    StageCancelled,
    StageContext,
    StageFailed,
    StageHandler,
    StageResult,
    only_processed,
    skipped_unpublished,
)
from reel.stages.cleaning import CLEANED_FILE

log = logging.getLogger("reel.stage.publishing")

# Shown in dry-run previews instead of a video link; never stored as a target's url.
PREVIEW_URL = "https://www.youtube.com/watch?v=reel-dry-run"
QUOTA_RESET_ZONE = ZoneInfo("America/Los_Angeles")  # YouTube quotas reset at its midnight
QUOTA_MARGIN_SECONDS = 600
QUOTA_REASONS = frozenset({"quotaExceeded", "uploadLimitExceeded"})
POST_TARGETS = (TargetKind.SLACK, TargetKind.CLASSROOM, TargetKind.LIBRARY)
LABELS = {
    TargetKind.YOUTUBE: "YouTube",
    TargetKind.SLACK: "Slack",
    TargetKind.CLASSROOM: "Classroom",
    TargetKind.LIBRARY: "Library",
}

TokensFactory = Callable[[LoadedConfig], AccessTokens]
YoutubeFactory = Callable[[YoutubeSettings, AccessTokens | None], YoutubePublisher]
SlackFactory = Callable[[SlackSettings, str | None], SlackPublisher]
ClassroomFactory = Callable[[ClassroomSettings, AccessTokens | None], ClassroomPublisher]
LibraryFactory = Callable[[LoadedConfig, Database, bool], LibraryPublisher]


def seconds_until_quota_reset(now: datetime | None = None) -> float:
    """Seconds until the YouTube quota resets (next midnight Pacific Time), plus a margin."""
    local = (now or datetime.now(UTC)).astimezone(QUOTA_RESET_ZONE)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(0), QUOTA_RESET_ZONE)
    # Subtract in UTC: aware datetimes with the same zone subtract as wall-clock times.
    wait = midnight.astimezone(UTC) - local.astimezone(UTC)
    return wait.total_seconds() + QUOTA_MARGIN_SECONDS


# --- What the stages read ----------------------------------------------------------------


@dataclass(frozen=True)
class Lesson:
    """The facts about one job that the publishers need."""

    job_id: int
    instance_id: str
    group_name: str
    group: GroupConfig
    lesson_number: int | None
    recording_date: date | None
    title: str
    summary: str
    description: str
    chapters: tuple[dict[str, Any], ...]
    thumbnail: Path | None
    duration: float | None

    @property
    def marker(self) -> str:
        """The YouTube tag that finds this job's video again; unique per database."""
        return f"reel-{self.instance_id[:12]}-job-{self.job_id}"

    def fields(self, url: str) -> dict[str, Any]:
        """The ``{placeholders}`` of the Slack and Classroom templates (POST_FIELDS)."""
        duration = ""
        if self.duration is not None:
            duration = format_timestamp(self.duration, long=self.duration >= 3600)
        return {
            "title": self.title,
            "url": url,
            "summary": self.summary,
            "group": self.group_name,
            "lesson": "" if self.lesson_number is None else self.lesson_number,
            "date": "" if self.recording_date is None else self.recording_date.isoformat(),
            "duration": duration,
        }


def load_lesson(ctx: StageContext) -> Lesson:
    with ctx.db.transaction() as session:
        job = session.get(Job, ctx.job_id)
        recording, meta, cut = job.recording, job.video_metadata, job.latest_cut_list
        instance = session.get(AppMeta, "instance_id")
        instance_id = instance.value if instance else ""
        group_name = recording.group_name or ""
        lesson_number, recording_date = recording.lesson_number, recording.recording_date
        duration = cut.final_duration_seconds if cut else None
        metadata = None
        if meta is not None:
            metadata = (
                meta.title,
                meta.summary or "",
                meta.description or "",
                tuple(dict(chapter) for chapter in meta.chapters or ()),
                meta.thumbnail_path,
            )
    if not instance_id:
        raise StageFailed("the database has no instance id; run 'reel db init'", retryable=False)
    group = ctx.config.groups.groups.get(group_name) if group_name else None
    if group is None:  # the chapters stage holds such jobs; this is a safety net
        raise Hold("identification", missing_group_message(group_name))
    if metadata is None:
        raise StageFailed(
            "the video has no title and description yet; run Chapters again", retryable=False
        )
    title, summary, description, chapters, thumbnail = metadata
    thumbnail_path = Path(thumbnail) if thumbnail else None
    if thumbnail_path is not None and not thumbnail_path.is_file():
        log.warning("job %s: the thumbnail %s is missing; uploading without it",
                    ctx.job_id, thumbnail_path.name)  # fmt: skip
        thumbnail_path = None
    return Lesson(
        ctx.job_id, instance_id, group_name, group, lesson_number, recording_date,
        title, summary, description, chapters, thumbnail_path, duration,
    )  # fmt: skip


# --- Publication rows ---------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    """A copy of one ``publication_targets`` row (or of no row)."""

    status: PublicationStatus | None = None
    dry_run: bool = False
    external_id: str | None = None
    url: str | None = None
    details: dict[str, Any] | None = None

    @property
    def live(self) -> bool:
        """A real publication exists (a video, a message, a post or an entry)."""
        return not self.dry_run and self.external_id is not None

    @property
    def state(self) -> dict[str, Any]:
        """The publisher's own saved state, without Reel's keys."""
        return {k: v for k, v in (self.details or {}).items() if k not in {"preview", "input"}}

    def unchanged(self, key: str) -> bool:
        """Already published (or skipped) from exactly the same input."""
        details = self.details or {}
        stored = {details.get("input"), (details.get("preview") or {}).get("input")}
        return self.status in {PublicationStatus.PUBLISHED, PublicationStatus.SKIPPED} and (
            key in stored
        )

    def earlier(self, target: TargetKind) -> Outcome:
        """The outcome of an unchanged target: what an earlier attempt already did."""
        if self.status is PublicationStatus.SKIPPED:
            return Outcome(target, "skipped")
        details = self.details or {}
        preview = self.dry_run or (details.get("preview") or {}).get("dry_run") is True
        return Outcome(target, "dry-run" if preview and not self.live else "published")


def _owned(session: Session, ctx: StageContext) -> None:
    """Stop if this attempt no longer holds the job (cancelled, or its lease expired)."""
    run = session.get(StageRun, ctx.claim.stage_run_id)
    job = session.get(Job, ctx.job_id)
    if (
        run is None
        or job is None
        or run.status is not StageRunStatus.RUNNING
        or job.lease_owner != ctx.claim.worker
    ):
        raise StageCancelled("this attempt no longer holds the job")


@contextmanager
def _row(ctx: StageContext, target: TargetKind) -> Iterator[tuple[Session, PublicationTarget]]:
    """The target's row in a write transaction, after the ownership check."""
    ctx.check()
    with ctx.db.transaction(immediate=True) as session:
        _owned(session, ctx)
        row = session.scalar(
            select(PublicationTarget).where(
                PublicationTarget.job_id == ctx.job_id, PublicationTarget.target == target
            )
        )
        if row is None:
            row = PublicationTarget(
                job_id=ctx.job_id,
                target=target,
                status=PublicationStatus.PENDING,
                dry_run=False,
                attempts=0,
                details={},
            )
            session.add(row)
        yield session, row


def read_record(db: Database, job_id: int, target: TargetKind) -> Record:
    with db.transaction() as session:
        row = session.scalar(
            select(PublicationTarget).where(
                PublicationTarget.job_id == job_id, PublicationTarget.target == target
            )
        )
        if row is None:
            return Record()
        return Record(row.status, row.dry_run, row.external_id, row.url, dict(row.details or {}))


def _is_live(row: PublicationTarget) -> bool:
    return not row.dry_run and row.external_id is not None


def _put_details(row: PublicationTarget, details: dict[str, Any], *, dry_run: bool) -> None:
    """Store a publisher's state. A dry-run preview never replaces a real publication."""
    if dry_run and _is_live(row):
        row.details = {**row.details, "preview": details}
    else:
        row.details = details


def _begin(ctx: StageContext, target: TargetKind) -> None:
    with _row(ctx, target) as (_, row):
        row.status = PublicationStatus.IN_PROGRESS
        row.attempts += 1
        row.last_error = None


def _save_state(ctx: StageContext, target: TargetKind, state: dict[str, Any]) -> None:
    with _row(ctx, target) as (_, row):
        _put_details(row, state, dry_run=bool(state.get("dry_run")))


def _published(
    ctx: StageContext,
    target: TargetKind,
    *,
    dry_run: bool,
    external_id: str | None,
    url: str | None,
    details: dict[str, Any],
    flags: tuple[str, ...] = (),
) -> None:
    with _row(ctx, target) as (session, row):
        if dry_run and _is_live(row):
            row.details = {**row.details, "preview": details}
        else:
            row.dry_run = dry_run
            row.external_id = None if dry_run else external_id
            row.url = None if dry_run else url
            row.details = details
            row.published_at = None if dry_run else utcnow()
        row.status = PublicationStatus.PUBLISHED
        row.last_error = None
        if flags:
            job = session.get(Job, ctx.job_id)
            for flag in flags:
                job.add_flag(flag)


def _failed(
    ctx: StageContext, target: TargetKind, error: str, *, external_id: str | None = None
) -> None:
    with _row(ctx, target) as (_, row):
        row.status = PublicationStatus.FAILED
        row.last_error = error
        if external_id:  # e.g. the Slack message exists, only its link is missing
            row.external_id, row.dry_run = external_id, False


def _skipped(ctx: StageContext, target: TargetKind, reason: str, key: str) -> None:
    with _row(ctx, target) as (_, row):
        row.status = PublicationStatus.SKIPPED
        row.last_error = None
        row.details = {**row.details, "skipped": reason, "input": key}


def _input_key(*parts: Any) -> str:
    text = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _sha256(path: Path, check: Callable[[], None]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(8 * 1024 * 1024):
            digest.update(chunk)
            check()
    return digest.hexdigest()


def _youtube_publisher(settings: YoutubeSettings, tokens: AccessTokens | None) -> YoutubePublisher:
    return YoutubePublisher(settings, tokens)


def _slack_publisher(settings: SlackSettings, token: str | None) -> SlackPublisher:
    return SlackPublisher(settings, token=token)


def _classroom_publisher(
    settings: ClassroomSettings, tokens: AccessTokens | None
) -> ClassroomPublisher:
    return ClassroomPublisher(settings, tokens)


def _library_publisher(cfg: LoadedConfig, db: Database, dry_run: bool) -> LibraryPublisher:
    return make_library_publisher(cfg, db, dry_run=dry_run)


# --- Upload -------------------------------------------------------------------------------


class UploadStage(StageHandler):
    name = Stage.UPLOAD

    def __init__(
        self,
        publisher_factory: YoutubeFactory = _youtube_publisher,
        tokens_factory: TokensFactory = make_google_tokens,
    ) -> None:
        self.publisher_factory = publisher_factory
        self.tokens_factory = tokens_factory

    def run(self, ctx: StageContext) -> StageResult:
        if only_processed(ctx):
            return skipped_unpublished()
        lesson = load_lesson(ctx)
        settings = ctx.config.settings.youtube
        video = ctx.job_dir / CLEANED_FILE
        if not video.is_file():
            raise StageFailed("the cleaned video is missing; run Cleaned again", retryable=False)
        ctx.report_progress(0, None, "checking the video file")
        sha = _sha256(video, ctx.check)
        previous = read_record(ctx.db, ctx.job_id, TargetKind.YOUTUBE).state or None
        upload = YoutubeUpload(
            ctx.job_id, lesson.marker, video, sha, lesson.title, lesson.description,
            thumbnail=lesson.thumbnail, playlist_id=lesson.group.youtube_playlist_id,
            group_name=lesson.group_name,
        )  # fmt: skip
        _begin(ctx, TargetKind.YOUTUBE)
        try:
            tokens = self.tokens_factory(ctx.config) if settings.mode == "live" else None
            publisher = self.publisher_factory(settings, tokens)
            result = publisher.publish(
                upload,
                previous,
                on_state=lambda state: _save_state(ctx, TargetKind.YOUTUBE, state),
                on_progress=lambda sent, total: ctx.report_progress(sent, total, "uploading"),
                check=ctx.check,
            )
        except GoogleApiError as error:
            _failed(ctx, TargetKind.YOUTUBE, str(error))
            quota = error.reason in QUOTA_REASONS
            raise StageFailed(
                f"YouTube: {error}",
                retryable=error.retryable,
                retry_after=seconds_until_quota_reset() if quota else None,
            ) from None
        except StageCancelled:
            raise
        except Exception as exc:  # e.g. the secret store: retried like any other failure
            _failed(ctx, TargetKind.YOUTUBE, f"{type(exc).__name__}: {exc}")
            raise
        _published(
            ctx, TargetKind.YOUTUBE, dry_run=result.dry_run, external_id=result.video_id or None,
            url=result.url or None, details=result.details, flags=result.flags,
        )  # fmt: skip
        size = video.stat().st_size
        metrics = {
            "dry_run": result.dry_run,
            "video_id": None if result.dry_run else result.video_id,
            "created": result.created,
            "replaced": list(result.replaced_video_ids),
            "flags": list(result.flags),
            "size_bytes": size,
        }
        if result.dry_run:
            message = "dry-run: nothing was uploaded (the plan is saved)"
        else:
            verb = "uploaded" if result.created else "updated"
            message = f"{verb} as unlisted: {result.url}"
        return StageResult(metrics=metrics, message=message)


# --- Post ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    target: TargetKind
    state: str  # published, dry-run, skipped, failed
    error: str = ""
    retryable: bool = True
    retry_after: float | None = None


class PostStage(StageHandler):
    name = Stage.POST

    def __init__(
        self,
        slack_factory: SlackFactory = _slack_publisher,
        classroom_factory: ClassroomFactory = _classroom_publisher,
        library_factory: LibraryFactory = _library_publisher,
        tokens_factory: TokensFactory = make_google_tokens,
    ) -> None:
        self.slack_factory = slack_factory
        self.classroom_factory = classroom_factory
        self.library_factory = library_factory
        self.tokens_factory = tokens_factory

    def run(self, ctx: StageContext) -> StageResult:
        if only_processed(ctx):
            return skipped_unpublished()
        lesson = load_lesson(ctx)
        video = read_record(ctx.db, ctx.job_id, TargetKind.YOUTUBE)
        if video.status is not PublicationStatus.PUBLISHED:
            raise StageFailed("the video is not uploaded; run Uploaded again", retryable=False)
        preview = not video.live  # no real link: every post is a preview (D-048)
        url = PREVIEW_URL if preview else str(video.url)
        tokens: list[AccessTokens] = []

        def google() -> AccessTokens:  # made once, only when a live Google post needs it
            if not tokens:
                tokens.append(self.tokens_factory(ctx.config))
            return tokens[0]

        steps = {
            TargetKind.SLACK: lambda: self._slack(ctx, lesson, url, preview),
            TargetKind.CLASSROOM: lambda: self._classroom(ctx, lesson, url, preview, google),
            TargetKind.LIBRARY: lambda: self._library(ctx, lesson, url, preview),
        }
        outcomes: list[Outcome] = []
        for target in POST_TARGETS:
            ctx.check()
            ctx.report_progress(len(outcomes), len(POST_TARGETS), LABELS[target])
            try:
                outcomes.append(steps[target]())
            except StageCancelled:
                raise
            except Exception as exc:  # a bug in one target must not block the others
                log.exception("job %s: %s failed unexpectedly", ctx.job_id, LABELS[target])
                error = f"{type(exc).__name__}: {exc}"
                _failed(ctx, target, error)
                outcomes.append(Outcome(target, "failed", error))
        metrics: dict[str, Any] = {o.target.value: o.state for o in outcomes}
        metrics["preview"] = preview
        summary = ", ".join(f"{LABELS[o.target]}: {o.state}" for o in outcomes)
        failed = [o for o in outcomes if o.state == "failed"]
        if failed:
            hints = [o.retry_after for o in failed if o.retry_after is not None]
            raise StageFailed(
                "; ".join(f"{LABELS[o.target]}: {o.error}" for o in failed) + f" ({summary})",
                retryable=any(o.retryable for o in failed),
                retry_after=max(hints) if hints else None,
            )
        return StageResult(metrics=metrics, message=summary)

    def _slack(self, ctx: StageContext, lesson: Lesson, url: str, preview: bool) -> Outcome:
        target, settings = TargetKind.SLACK, ctx.config.settings.slack
        channel = lesson.group.slack_channel.strip()
        dry_run = preview or settings.mode == "dry-run"
        fields = lesson.fields(url)
        key = _input_key(target, dry_run, channel, settings.message_template, fields)
        record = read_record(ctx.db, ctx.job_id, target)
        if not channel:
            _skipped(ctx, target, "no Slack channel is set for the group", key)
            return Outcome(target, "skipped")
        if record.unchanged(key):
            return record.earlier(target)
        if dry_run:
            settings = settings.model_copy(update={"mode": "dry-run"})
        previous = None
        if record.live and str(record.external_id).partition(":")[0] == channel:
            previous = record.external_id  # update this message instead of posting again
        post = SlackPost(ctx.job_id, channel, instance_id=lesson.instance_id, **fields)
        _begin(ctx, target)
        try:
            publisher = self.slack_factory(settings, ctx.config.secrets.get("SLACK_BOT_TOKEN"))
            result = publisher.publish(post, previous)
        except SlackError as error:
            _failed(ctx, target, str(error), external_id=error.external_id)
            return Outcome(target, "failed", str(error), error.retryable, error.retry_after)
        details = {"input": key, "channel": channel, "text": result.text, "dry_run": dry_run}
        _published(ctx, target, dry_run=result.dry_run, external_id=result.external_id,
                   url=result.url, details=details)  # fmt: skip
        return Outcome(target, "dry-run" if result.dry_run else "published")

    def _classroom(
        self,
        ctx: StageContext,
        lesson: Lesson,
        url: str,
        preview: bool,
        google: Callable[[], AccessTokens],
    ) -> Outcome:
        target, settings = TargetKind.CLASSROOM, ctx.config.settings.classroom
        course = lesson.group.classroom_course_id.strip()
        dry_run = preview or settings.mode == "dry-run"
        fields = lesson.fields(url)
        key = _input_key(target, dry_run, course, settings.post_type, settings.text_template,
                         fields)  # fmt: skip
        record = read_record(ctx.db, ctx.job_id, target)
        if not course:
            _skipped(ctx, target, "no Classroom course is set for the group", key)
            return Outcome(target, "skipped")
        if record.unchanged(key):
            return record.earlier(target)
        if dry_run:
            settings = settings.model_copy(update={"mode": "dry-run"})
        state = record.state
        previous = state if state.get("course_id") == course else None
        post = ClassroomPost(ctx.job_id, course, instance_id=lesson.instance_id, **fields)
        _begin(ctx, target)
        try:
            publisher = self.classroom_factory(settings, None if dry_run else google())
            result = publisher.publish(
                post,
                previous,
                on_state=lambda snapshot: _save_state(ctx, target, snapshot),
                check=ctx.check,
            )
        except GoogleApiError as error:
            _failed(ctx, target, str(error))
            return Outcome(target, "failed", str(error), error.retryable)
        _published(ctx, target, dry_run=result.dry_run, external_id=result.post_id,
                   url=result.url, details={**result.details, "input": key})  # fmt: skip
        return Outcome(target, "dry-run" if result.dry_run else "published")

    def _library(self, ctx: StageContext, lesson: Lesson, url: str, preview: bool) -> Outcome:
        target, provider = TargetKind.LIBRARY, ctx.config.settings.library.provider
        record = read_record(ctx.db, ctx.job_id, target)
        try:
            item = LibraryItem(
                title=lesson.title,
                video_url=url,
                group=lesson.group_name,
                lesson_number=lesson.lesson_number,
                recording_date=lesson.recording_date,
                chapters=[{"start_seconds": float(c["start"]), "title": c["title"]}
                          for c in lesson.chapters],
            )  # fmt: skip
        except (ValueError, KeyError, TypeError) as exc:
            error = f"the library entry is not valid: {exc}"
            _failed(ctx, target, error)
            return Outcome(target, "failed", error, retryable=False)
        key = _input_key(target, preview, provider, item.to_dict())
        if record.unchanged(key):
            return record.earlier(target)
        _begin(ctx, target)
        try:
            result = self.library_factory(ctx.config, ctx.db, preview).publish(ctx.job_id, item)
        except LibraryError as error:
            _failed(ctx, target, str(error))
            return Outcome(target, "failed", str(error), error.retryable, error.retry_after)
        details: dict[str, Any] = {"input": key, "provider": provider, "dry_run": result.dry_run}
        if result.external_key:
            details["external_key"] = result.external_key
        if result.dry_run:
            details["payload"] = result.payload
        _published(ctx, target, dry_run=result.dry_run, external_id=result.entry_id,
                   url=result.url, details=details)  # fmt: skip
        return Outcome(target, "dry-run" if result.dry_run else "published")
