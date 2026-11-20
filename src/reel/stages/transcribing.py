"""Stage ``transcribe`` (T-302; FR-13, FR-14): the raw-timeline transcript of the lesson.

Reads ``data/jobs/<id>/audio.wav`` (made by import), uses the group's vocabulary as the
recognition prompt, reports progress (seconds of audio) through the heartbeat and every 10 %
in the job log, and stores the result as ``transcript.raw.json`` and a ``raw`` transcript row.
"""

import time
from collections.abc import Callable
from dataclasses import replace

from reel.config.loader import LoadedConfig
from reel.db import Job, Stage, TranscriptKind
from reel.queue.stage import StageContext, StageFailed, StageHandler, StageResult
from reel.stages.importing import AUDIO_FILE
from reel.stt import Transcriber, TranscriptError, save_transcript, wav_duration, write_json
from reel.stt.whisper import ModelMissing, make_transcriber

RAW_FILE = "transcript.raw.json"
EMPTY = "empty_transcript"


class TranscribeStage(StageHandler):
    name = Stage.TRANSCRIBE
    heavy = True

    def __init__(self, factory: Callable[[LoadedConfig], Transcriber] = make_transcriber) -> None:
        self.factory = factory

    def run(self, ctx: StageContext) -> StageResult:
        audio = ctx.job_dir / AUDIO_FILE
        if not audio.is_file():
            raise StageFailed(f"{audio} is missing; re-run the job from Imported", retryable=False)
        try:
            total = wav_duration(audio)
        except TranscriptError as exc:
            raise StageFailed(f"{exc}; re-run the job from Imported", retryable=False) from exc
        with ctx.db.transaction() as session:
            group = session.get(Job, ctx.job_id).recording.group_name
        stt = ctx.config.settings.stt
        found = ctx.config.groups.groups.get(group) if group else None
        vocabulary = found.vocabulary if found else ""
        prompt = ", ".join(
            text for text in (stt.initial_prompt.strip(), vocabulary.strip()) if text
        )
        try:
            transcriber = self.factory(ctx.config)
        except ModelMissing as exc:
            raise StageFailed(str(exc), retryable=False) from exc

        logged = -1

        def progress(done: float) -> None:
            nonlocal logged
            ctx.report_progress(done, total, "seconds of audio transcribed")
            tenth = min(int(done / total * 10), 10) if total else 10
            if tenth > logged:
                logged = tenth
                ctx.log.info("transcribed %d%% (%.0f of %.0f s)", tenth * 10, done, total)

        started = time.monotonic()
        result = transcriber.transcribe(
            audio, language=stt.language, prompt=prompt, on_progress=progress, check=ctx.check
        )
        ctx.check()
        seconds = time.monotonic() - started
        result = replace(
            result,
            details={
                **result.details,
                "seconds": round(seconds, 3),
                "realtime_factor": round(seconds / total, 4) if total else None,
            },
        )
        write_json(ctx.job_dir / RAW_FILE, result)
        word_count = sum(len(segment.words) or 1 for segment in result.segments)
        with ctx.db.transaction(immediate=True) as session:
            save_transcript(session, ctx.job_id, TranscriptKind.RAW, result)
            if not result.segments:  # nothing recognised: staff should look at it
                session.get(Job, ctx.job_id).add_flag(EMPTY)
        return StageResult(
            metrics={
                "audio_seconds": round(total, 3),
                "seconds": round(seconds, 3),
                "segments": len(result.segments),
                "words": word_count,
                "model": result.model,
            },
            message=f"{len(result.segments)} segments, {word_count} words in {seconds:.0f} s",
        )
