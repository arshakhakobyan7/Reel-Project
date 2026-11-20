"""Stage ``chapters`` (T-504; FR-15 … FR-19, FR-3): everything needed to publish the video.

1. **Identification gate:** without a known group and a lesson number the job goes on hold
   ("identification") and staff are notified; after they set them and release the job, the
   stage runs again (D-018).
2. Chapters and the topic summary from the clean transcript (T-501, D-044).
3. Title and description (T-502): the title template, the summary, the chapter list, the footer.
4. Thumbnail from the cleaned video (T-503): the main topic as the big text, the video title
   below it. A thumbnail that cannot be made is a flag, not a failure (it is optional, FR-18).
5. One ``video_metadata`` row (replaced on a re-run) and ``metadata.json`` in the job folder.
"""

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from reel.config.groups import missing_group_message
from reel.config.loader import LoadedConfig
from reel.db import Job, Stage, TranscriptKind, VideoMetadata
from reel.llm import LLMClient, LLMError, MeteredLLM, OnDemandLLM
from reel.media.ffmpeg import FfmpegCancelled, FfmpegError, probe
from reel.media.thumbnail import MissingGlyphs, make_thumbnail
from reel.metadata.chapters import NOT_GENERATED, ChapterResult, generate_chapters, topic_summary
from reel.metadata.text import MetadataTextError, build_description, render_title
from reel.queue.stage import Hold, StageContext, StageFailed, StageHandler, StageResult
from reel.stages.cleaning import CLEANED_FILE
from reel.stt import load_transcript

log = logging.getLogger("reel.metadata")

THUMBNAIL_FILE = "thumbnail.jpg"
METADATA_FILE = "metadata.json"
IDENTIFICATION = "identification"
NO_THUMBNAIL = "thumbnail_not_made"


def main_topic(result: ChapterResult) -> str:
    """The title of the longest chapter (the lesson's main topic), or the commonest label."""
    chapters = result.chapters
    if chapters:
        ends = [c.start for c in chapters[1:]] + [result.details.get("duration", 0.0)]
        longest = max(zip(chapters, ends, strict=True), key=lambda pair: pair[1] - pair[0].start)
        return longest[0].title
    labels = [w["label"] for w in result.details.get("windows", []) if w.get("label")]
    return max(set(labels), key=labels.count) if labels else ""


class ChaptersStage(StageHandler):
    name = Stage.CHAPTERS
    heavy = True  # the local LLM

    def __init__(self, llm_factory: Callable[[LoadedConfig], LLMClient] = OnDemandLLM) -> None:
        self.llm_factory = llm_factory

    def run(self, ctx: StageContext) -> StageResult:
        cfg, settings = ctx.config, ctx.config.settings.metadata
        with ctx.db.transaction() as session:
            recording = session.get(Job, ctx.job_id).recording
            group_name, lesson = recording.group_name, recording.lesson_number
            recording_date = recording.recording_date
            transcript = load_transcript(session, ctx.job_id, TranscriptKind.CLEAN)
        group = cfg.groups.groups.get(group_name) if group_name else None
        if group is None:
            raise Hold(IDENTIFICATION, missing_group_message(group_name, lesson is None))
        if lesson is None:
            raise Hold(
                IDENTIFICATION, "Set the lesson number of this recording, then release the job."
            )
        video = ctx.job_dir / CLEANED_FILE
        if transcript is None or not video.is_file():
            raise StageFailed(
                "the cleaned video or its transcript is missing; re-run the job from Cleaned",
                retryable=False,
            )
        try:
            duration = probe(cfg.paths.ffprobe, video).duration_seconds or 0.0
        except FfmpegError as exc:
            raise StageFailed(f"could not read the cleaned video: {exc}") from exc
        started = time.monotonic()

        # Chapters and the topic summary (the LLM is started only if there is text).
        usage = None
        if transcript.segments:
            llm = MeteredLLM(self.llm_factory(cfg))
            try:
                result = generate_chapters(
                    transcript,
                    duration,
                    settings,
                    llm=llm,
                    prompts=cfg.prompts,
                    vocabulary=group.vocabulary,
                    check=ctx.check,
                )
            except LLMError as exc:
                raise StageFailed(f"chapters: {exc}", retryable=exc.retryable) from exc
            finally:
                usage = llm.usage()
                llm.close()
        else:
            result = ChapterResult([], [NOT_GENERATED], 0, {"rejected": "empty transcript"})
        result.details["duration"] = duration
        summary, topic = topic_summary(result), main_topic(result)

        # Title and description.
        fields = {
            "group": group_name,
            "lesson": lesson,
            "date": recording_date.isoformat() if recording_date else "",
        }
        try:
            title = render_title(settings, topic=topic, **fields)
            description = build_description(
                summary=summary,
                chapters=result.chapters,
                duration=duration,
                footer_fields={**fields, "title": title},
                settings=settings,
            )
        except MetadataTextError as exc:
            raise StageFailed(f"title/description: {exc}", retryable=False) from exc
        flags = [*result.flags, *description.flags]

        # Thumbnail.
        thumbnail = None
        if settings.thumbnail_enabled:
            thumbnail = self._thumbnail(ctx, video, topic, title, flags)

        chapters = [{"start": round(c.start, 3), "title": c.title} for c in result.chapters]
        generator = {
            "llm_calls": result.llm_calls,
            "chapter_details": result.details,
            "topic": topic,
            "flags": flags,
            "seconds": round(time.monotonic() - started, 1),
        }
        _write_json(
            ctx.job_dir / METADATA_FILE,
            {"title": title, "summary": summary, "description": description.text,
             "chapters": chapters, "thumbnail": thumbnail, "generator": generator},
        )  # fmt: skip
        ctx.check()
        with ctx.db.transaction(immediate=True) as session:
            job = session.get(Job, ctx.job_id)
            row = job.video_metadata or VideoMetadata(job_id=ctx.job_id, title=title)
            row.title, row.summary, row.description = title, summary, description.text
            row.chapters, row.thumbnail_path, row.generator = chapters, thumbnail, generator
            session.add(row)
            for flag in flags:
                job.add_flag(flag)
        return StageResult(
            metrics={
                "chapters": len(chapters),
                "llm_calls": result.llm_calls,
                "llm": usage,
                "thumbnail": thumbnail is not None,
                "flags": flags,
                "seconds": generator["seconds"],
            },
            message=f"{title!r}: {len(chapters)} chapters"
            + ("" if thumbnail else ", no thumbnail"),
        )

    def _thumbnail(
        self, ctx: StageContext, video: Path, topic: str, title: str, flags: list[str]
    ) -> str | None:
        paths = ctx.config.paths
        target = ctx.job_dir / THUMBNAIL_FILE
        # The main topic is the big text; if the font cannot draw it, the title alone.
        attempts = [(topic, title)] if topic else []
        attempts.append((title, ""))
        for big, small in attempts:
            try:
                make_thumbnail(
                    paths.ffmpeg, paths.ffprobe, video, big, target,
                    subtitle=small, on_cancel=ctx.on_cancel,
                )  # fmt: skip
                return str(target)
            except FfmpegCancelled:
                ctx.check()
                raise
            except MissingGlyphs as exc:
                log.warning("thumbnail text cannot be drawn: %s", exc)
            except FfmpegError as exc:  # ThumbnailError is an FfmpegError
                log.warning("thumbnail not made: %s", exc)
                break
        flags.append(NO_THUMBNAIL)
        return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    partial = path.with_name(path.name + ".part")
    partial.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), "utf-8")
    os.replace(partial, path)
