"""Stage ``import`` (T-204; FR-2, FR-4, FR-5, FR-12).

1. SHA-256 of the whole file, read in chunks (cancellable). The same content under another
   name or path is a duplicate: the job is cancelled with a reference to the original.
   A published recording is compared with the published ones (FR-4: nothing is published
   twice). A recording that is only processed (a user's upload, T-1013) is compared only with
   the same account's recordings that are in progress or ready, so another account's copy
   never blocks it, and a cancelled, failed or deleted one is processed again (T-1015).
2. Validation (``reel.media.ffmpeg.validate``): an unusable file fails at once, with the reason.
3. Media information, original duration and identification (group, lesson, date) are saved.
4. The audio track is extracted to ``data/jobs/<job id>/audio.wav`` for transcription.

A lesson uploaded in several parts (D-035, T-807) is hashed as one stream, every part is
checked, and the parts' audio is joined, each part cut or padded to exactly its duration so
that the transcript's times match the video timeline of the clean stage. Group, lesson and
date set by staff are kept; only values they left empty are filled from the file names.

Every step can run again: a re-run of the stage gives the same result.
"""

import hashlib
import logging
import os
import time
import wave
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from reel.config.loader import ResolvedPaths
from reel.db import Job, JobStatus, Recording, StaffUser, Stage
from reel.ingest.identify import MISSING_LESSON, Identification, identify_recording
from reel.media.ffmpeg import FfmpegCancelled, FfmpegError, InvalidMedia, extract_audio, validate
from reel.queue.stage import Discard, StageContext, StageFailed, StageHandler, StageResult

log = logging.getLogger("reel.stages")
AUDIO_FILE = "audio.wav"
SAMPLE_RATE = 16000  # extract_audio writes 16 kHz mono 16-bit
CHUNK_BYTES = 4 * 1024 * 1024


def sha256_file(path: Path, check: Callable[[], None] | None = None) -> str:
    """Streamed SHA-256 (lowercase hex). ``check`` runs after every chunk."""
    return sha256_files([path], check)


def sha256_files(paths: list[Path], check: Callable[[], None] | None = None) -> str:
    """SHA-256 of the files one after another (the same as one file for one path)."""
    digest = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as file:
            while chunk := file.read(CHUNK_BYTES):
                digest.update(chunk)
                if check:
                    check()
    return digest.hexdigest()


def join_audio(
    parts: list[Path], durations: list[float], target: Path, check: Callable[[], None]
) -> None:
    """Join 16 kHz mono 16-bit WAV parts; each part gives exactly ``duration`` seconds
    (cut, or padded with silence), so part boundaries match the video timeline."""
    partial = target.with_name(target.name + ".part")
    with wave.open(str(partial), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        for path, duration in zip(parts, durations, strict=True):
            left = round(duration * SAMPLE_RATE)
            with wave.open(str(path), "rb") as part:
                if (part.getnchannels(), part.getsampwidth(), part.getframerate()) != (
                    1,
                    2,
                    SAMPLE_RATE,
                ):
                    raise StageFailed(f"unexpected audio format in {path.name}")
                while left > 0:
                    frames = part.readframes(min(left, SAMPLE_RATE * 10))
                    if not frames:
                        break
                    out.writeframes(frames)
                    left -= len(frames) // 2
                    check()
            while left > 0:  # the audio track was shorter than the video
                step = min(left, SAMPLE_RATE * 10)
                out.writeframes(b"\0\0" * step)
                left -= step
    os.replace(partial, target)


def _fill_missing(recording: Recording, job: Job, found: Identification) -> None:
    """Staff set the identification: keep it, and fill only what they left empty."""
    if recording.lesson_number is None and found.lesson_number is not None:
        recording.lesson_number = found.lesson_number
    if recording.recording_date is None and found.recording_date is not None:
        recording.recording_date = found.recording_date
    if recording.lesson_number is None:
        job.add_flag(MISSING_LESSON)


class ImportStage(StageHandler):
    name = Stage.IMPORT

    def run(self, ctx: StageContext) -> StageResult:
        from reel.stages.cleaning import source_files  # the one place that knows the parts

        with ctx.db.transaction() as session:
            sources = source_files(session.get(Recording, ctx.recording_id))
        several = len(sources) > 1
        started = time.monotonic()
        try:
            content_hash = sha256_files(sources, ctx.check)
        except FileNotFoundError as exc:
            raise StageFailed(f"the recording no longer exists: {exc.filename}") from None
        except OSError as exc:
            raise StageFailed(f"cannot read the recording: {exc}") from exc
        hash_seconds = time.monotonic() - started
        self._claim_content(ctx, content_hash)

        paths, ingest = ctx.config.paths, ctx.config.settings.ingest
        minimum = ingest.min_duration_minutes * 60
        infos = []
        for source in sources:
            try:
                infos.append(
                    validate(
                        paths.ffmpeg,
                        paths.ffprobe,
                        source,
                        min_duration_seconds=0 if several else minimum,
                        on_cancel=ctx.on_cancel,
                    )
                )
            except FfmpegCancelled:
                ctx.check()
                raise
            except InvalidMedia as exc:
                name = f" (part {source.name})" if several else ""
                raise StageFailed(f"not a usable recording{name}: {exc}", retryable=False) from exc
            except FfmpegError as exc:  # FFmpeg itself failed to run: may work next time
                raise StageFailed(f"could not check the recording: {exc}") from exc
        durations = [info.duration_seconds or 0.0 for info in infos]
        total = sum(durations)
        if several and total < minimum:
            raise StageFailed(
                f"not a usable recording: {total / 60:.1f} min in all, shorter than "
                f"{ingest.min_duration_minutes:g} min",
                retryable=False,
            )

        with ctx.db.transaction(immediate=True) as session:
            recording = session.get(Recording, ctx.recording_id)
            media = infos[0].to_dict()
            if several:
                stored = {part.get("path"): part for part in recording.media_info["parts"]}
                media["parts"] = [
                    {**stored.get(str(path), {}), "path": str(path),
                     "duration_seconds": info.duration_seconds, "info": info.to_dict()}
                    for path, info in zip(sources, infos, strict=True)
                ]  # fmt: skip
            recording.media_info = media
            recording.original_duration_seconds = total
            if recording.file_modified_at is None:  # added by hand, not by the watcher
                mtime = sources[0].stat().st_mtime
                recording.file_modified_at = datetime.fromtimestamp(mtime, UTC)
            found = identify_recording(ctx.config, recording, creation_time=infos[0].creation_time)
            if recording.identification.get("set_by_staff"):  # staff values win
                _fill_missing(recording, recording.job, found)
            else:
                found.apply(recording, recording.job)
            group, lesson = recording.group_name, recording.lesson_number

        audio = ctx.job_dir / AUDIO_FILE
        pieces = [ctx.job_dir / f"audio-part{n + 1}.wav" for n in range(len(sources))]
        try:
            for source, piece, duration in zip(sources, pieces, durations, strict=True):
                extract_audio(
                    paths.ffmpeg, source, audio if not several else piece, duration=duration,
                    on_cancel=ctx.on_cancel,
                )  # fmt: skip
        except FfmpegCancelled:
            ctx.check()
            raise
        except FfmpegError as exc:
            raise StageFailed(f"could not extract the audio: {exc}") from exc
        if several:
            join_audio(pieces, durations, audio, ctx.check)
            for piece in pieces:
                piece.unlink(missing_ok=True)

        lesson_text = lesson if lesson is not None else "?"
        message = f"{total / 60:.1f} min, {group or 'unknown group'}, lesson {lesson_text}"
        if several:
            message += f", {len(sources)} parts"
        if found.flags:
            message += f" (flags: {', '.join(found.flags)})"
        return StageResult(
            metrics={
                "size_bytes": sum(source.stat().st_size for source in sources),
                "parts": len(sources),
                "hash_seconds": round(hash_seconds, 3),
                "duration_seconds": total,
                "audio_bytes": audio.stat().st_size,
            },
            message=message,
        )

    def _claim_content(self, ctx: StageContext, content_hash: str) -> None:
        """Save the hash, or cancel the job if another recording already has it (FR-4).

        The write lock is taken before the lookup, so two copies imported at the same time
        cannot both pass."""
        with ctx.db.transaction(immediate=True) as session:
            recording = session.get(Recording, ctx.recording_id)
            if recording.publish:
                original = session.scalars(
                    select(Recording).where(
                        Recording.content_hash == content_hash, Recording.id != recording.id
                    )
                ).first()
            else:
                original = own_original(session, recording, content_hash, ctx.config.paths)
            if original is None:
                recording.content_hash = content_hash if recording.publish else None
                recording.processed_hash = None if recording.publish else content_hash
                recording.duplicate_of_id = None
                return
            recording.duplicate_of_id = original.id
            job = f"job {original.job.id}" if original.job else "no job"
            details = f"recording {original.id} ({job}, {original.source_path})"
            message = f"duplicate of {details}; not processed again"
            if not _may_see(session, recording, original):
                # the uploader may not open the other account's recording (T-1015)
                log.info("job %s: duplicate of %s", ctx.job_id, details,
                         extra={"job_id": ctx.job_id})  # fmt: skip
                message = "duplicate of a recording of another account; not processed again"
        log.info("job %s: %s", ctx.job_id, message, extra={"job_id": ctx.job_id})
        raise Discard(message)


ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.ON_HOLD)


def own_original(
    session: Session, recording: Recording, content_hash: str, paths: ResolvedPaths
) -> Recording | None:
    """The same account's earlier recording of this content that is in progress, or ready
    with its processed video still here (T-1015). A cancelled or failed one, or one whose
    video was deleted (T-1014), does not count: the upload is processed again."""
    from reel.stages.cleaning import CLEANED_FILE

    candidates = session.scalars(
        select(Recording)
        .where(
            Recording.processed_hash == content_hash,
            Recording.id != recording.id,
            Recording.uploaded_by_id == recording.uploaded_by_id,
        )
        .order_by(Recording.id)
    )
    for candidate in candidates:
        job = candidate.job
        if job is None:
            continue
        if job.status in ACTIVE:
            return candidate
        video = paths.data_dir / "jobs" / str(job.id) / CLEANED_FILE
        if job.status is JobStatus.DONE and video.is_file():
            return candidate
    return None


def _may_see(session: Session, recording: Recording, original: Recording) -> bool:
    """Whoever uploaded ``recording`` may see ``original``: their own, or an admin."""
    if recording.uploaded_by_id is None or recording.uploaded_by_id == original.uploaded_by_id:
        return True
    uploader = session.get(StaffUser, recording.uploaded_by_id)
    return uploader is not None and uploader.is_admin
