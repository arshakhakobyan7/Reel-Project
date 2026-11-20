"""Stage ``clean`` (T-406; FR-6 … FR-13): the cleaned video and its transcript.

1. Lesson start and end from the raw transcript (T-402, D-039); the local LLM is started only
   if the phrases are not enough, and stopped again before the heavy work.
2. Nobody at the board for more than 10 s → 5 s (T-403, D-040), per recorded part.
3. Silences over a static picture (T-401, D-014), per recorded part.
4. The cut list (T-404, D-041) → ``cutlist.json``.
5. Rendering with loudness normalisation (T-405, D-042) → ``cleaned.mp4``.
6. The raw transcript mapped to the cleaned timeline → ``transcript.clean.json``.
The cut list, the clean transcript and the flags are stored together at the end; a summary of
every analysis is kept in ``cleaning.json`` so staff can see why something was cut (UJ-5).
"""

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from reel.cleaning.boundaries import detect_boundaries
from reel.cleaning.cutlist import (
    Cut,
    CutListError,
    Part,
    Timeline,
    boundary_cuts,
    build_cut_list,
    save_cut_list,
    write_cut_list,
)
from reel.cleaning.framing import Box, Crop, crop_rect, union
from reel.cleaning.presence import Detector, ModelMissing, analyze_board
from reel.cleaning.remap import remap_transcript
from reel.cleaning.silence import analyze_silence
from reel.config.loader import LoadedConfig
from reel.db import Job, Recording, Stage, TranscriptKind
from reel.llm import LLMClient, LLMError, MeteredLLM, OnDemandLLM
from reel.media.ffmpeg import FfmpegCancelled, FfmpegError, probe
from reel.media.render import render
from reel.queue.stage import StageContext, StageFailed, StageHandler, StageResult
from reel.stt import load_transcript, save_transcript, write_json

CLEANED_FILE = "cleaned.mp4"
CUT_LIST_FILE = "cutlist.json"
CLEAN_FILE = "transcript.clean.json"
SUMMARY_FILE = "cleaning.json"

# Share of the stage's work, for the progress bar.
log = logging.getLogger("reel.cleaning")
WEIGHTS = {"board": 0.15, "silence": 0.20, "render": 0.65}


def source_files(recording: Recording) -> list[Path]:
    """The recorded files of the lesson in order: ``media_info["parts"]`` for a lesson uploaded
    in several parts (D-035, T-807), otherwise the one source file."""
    parts = (recording.media_info or {}).get("parts")
    if parts:
        return [Path(part["path"]) for part in parts]
    return [Path(recording.source_path)]


def _clock(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes // 60}:{minutes % 60:02d}:{rest:02d}"


class CleanStage(StageHandler):
    name = Stage.CLEAN
    heavy = True

    def __init__(
        self,
        *,
        llm_factory: Callable[[LoadedConfig], LLMClient] = OnDemandLLM,
        detector: Detector | None = None,
    ) -> None:
        self.llm_factory = llm_factory
        self.detector = detector  # tests; None = the YOLOX person detector

    def run(self, ctx: StageContext) -> StageResult:
        cfg, settings = ctx.config, ctx.config.settings
        paths = cfg.paths
        with ctx.db.transaction() as session:
            recording = session.get(Job, ctx.job_id).recording
            sources = source_files(recording)
            transcript = load_transcript(session, ctx.job_id, TranscriptKind.RAW)
        if transcript is None:
            raise StageFailed(
                "the raw transcript is missing; re-run the job from Transcribed", retryable=False
            )
        for path in sources:
            if not path.is_file():
                raise StageFailed(f"the recording file {path} is missing", retryable=False)
        timings: dict[str, float] = {}
        done = 0.0

        def progress(step: str, share: float | None) -> None:
            ctx.report_progress(round(100 * (done + WEIGHTS[step] * (share or 0)), 1), 100, step)

        def media(action: str, work: Callable[[], Any]) -> Any:
            try:
                return work()
            except FfmpegCancelled:
                ctx.check()
                raise
            except FfmpegError as exc:
                raise StageFailed(f"{action}: {exc}") from exc

        started = time.monotonic()
        infos = [media("could not read the recording", lambda p=p: probe(paths.ffprobe, p))
                 for p in sources]  # fmt: skip
        timeline = Timeline(
            [
                Part(info.duration_seconds or 0.0, info.fps, path.name)
                for path, info in zip(sources, infos, strict=True)
            ]
        )

        # 1. Lesson start and end.
        clock = time.monotonic()
        boundary_settings = settings.cleaning.boundaries
        llm = MeteredLLM(self.llm_factory(cfg)) if boundary_settings.use_llm else None
        try:
            boundaries = detect_boundaries(
                transcript,
                boundary_settings,
                duration=timeline.duration,
                llm=llm,
                prompts=cfg.prompts,
                check=ctx.check,
            )
        except LLMError as exc:
            raise StageFailed(f"lesson start/end: {exc}", retryable=exc.retryable) from exc
        finally:
            if hasattr(llm, "close"):
                llm.close()
        timings["boundaries"] = time.monotonic() - clock
        cuts = boundary_cuts(boundaries, timeline.duration)
        summary: dict[str, Any] = {"boundaries": boundaries.to_dict(), "board": [], "silence": []}

        # 2. Nobody at the board; the same pictures give the board framing (D-058).
        clock = time.monotonic()
        board = settings.cleaning.empty_board
        framing = settings.cleaning.framing
        crops: list[Crop | None] = [None] * len(sources)
        zooms: list[float | None] = [None] * len(sources)
        found: list[tuple[int, list[int], Any]] = []
        summary["framing"] = []
        if board.enabled or framing.enabled:
            for index, path in enumerate(sources):
                share = 1 / len(sources)
                try:
                    analysis = media(
                        "board check",
                        lambda path=path, index=index, share=share: analyze_board(
                            paths.ffmpeg, paths.ffprobe, path, board,
                            models_dir=paths.models_dir, detector=self.detector,
                            framing=framing if framing.enabled else None,
                            on_progress=lambda _d, s: progress("board", (index + (s or 0)) * share),
                            on_cancel=ctx.on_cancel,
                        ),
                    )  # fmt: skip
                except ModelMissing as exc:
                    raise StageFailed(str(exc), retryable=False) from exc
                if board.enabled:
                    cuts += [
                        Cut(
                            timeline.to_raw(index, r.start), timeline.to_raw(index, r.end), r.reason
                        )  # fmt: skip
                        for r in analysis.removals
                    ]
                    summary["board"].append(
                        {
                            "part": index,
                            "pictures": len(analysis.samples),
                            "mode": analysis.mode,
                            "empty": [[e.start, e.end] for e in analysis.empty],
                        }
                    )
                if analysis.framing is not None:
                    found.append((index, analysis.details["source_size"], analysis.framing))
            # One camera (all parts the same size): one crop around all the work, so the
            # picture does not jump at the joins. Otherwise each part gets its own.
            same = len({tuple(size) for _, size, _ in found}) == 1
            together: Box | None = union([f.frame for _, _, f in found]) if same else None
            for index, (width, height), result in found:
                box = together if same else result.frame
                crops[index], note = crop_rect(box, width, height, framing)
                crop = crops[index]
                zooms[index] = None if crop is None else round(crop.zoom(width), 2)
                summary["framing"].append({
                    "part": index,
                    **result.to_dict(),
                    "crop": None if crop is None else [crop.x, crop.y, crop.width, crop.height],
                    "crop_note": note,
                    "source_size": [width, height],
                    "shared": same and len(found) > 1,
                })  # fmt: skip
                log.info("framing of %s: %s (%s)", sources[index].name, note, result.note)
        done += WEIGHTS["board"]
        timings["board"] = time.monotonic() - clock

        # 3. Silences over a static picture.
        clock = time.monotonic()
        for index, path in enumerate(sources):
            share = 1 / len(sources)
            silence = media(
                "silence analysis",
                lambda path=path, index=index, share=share: analyze_silence(
                    paths.ffmpeg, paths.ffprobe, path, settings.cleaning.silence,
                    on_progress=lambda _d, s: progress("silence", (index + (s or 0)) * share),
                    on_cancel=ctx.on_cancel,
                ),
            )  # fmt: skip
            cuts += [
                Cut(timeline.to_raw(index, r.start), timeline.to_raw(index, r.end), r.reason)
                for r in silence.removals
            ]
            summary["silence"].append(
                {"part": index, "removals": [[r.start, r.end, r.reason] for r in silence.removals]}
            )
        done += WEIGHTS["silence"]
        timings["silence"] = time.monotonic() - clock

        # 4. The cut list.
        try:
            cut_list = build_cut_list(timeline, cuts, settings.cleaning.cuts)
        except CutListError as exc:
            raise StageFailed(f"cut list: {exc}", retryable=False) from exc
        write_cut_list(ctx.job_dir / CUT_LIST_FILE, cut_list)
        ctx.check()

        # 5. The video.
        clock = time.monotonic()
        result = media(
            "rendering",
            lambda: render(
                paths.ffmpeg, paths.ffprobe, sources, cut_list, ctx.job_dir / CLEANED_FILE,
                settings.render, settings.audio, fade_ms=settings.cleaning.cuts.fade_ms,
                work_dir=ctx.job_dir, crops=crops,
                on_progress=lambda _d, s: progress("render", s),
                on_cancel=ctx.on_cancel,
            ),
        )  # fmt: skip
        timings["render"] = time.monotonic() - clock

        # 6. The clean transcript; everything is stored together.
        clean = remap_transcript(transcript, cut_list)
        write_json(ctx.job_dir / CLEAN_FILE, clean)
        summary["cut_list"] = {
            "original": cut_list.original_duration,
            "final": cut_list.final_duration,
            "removed": [[s.start, s.end, s.reason] for s in cut_list.removed],
        }
        _write_summary(ctx.job_dir / SUMMARY_FILE, summary)
        ctx.check()
        with ctx.db.transaction(immediate=True) as session:
            row = save_cut_list(session, ctx.job_id, cut_list)
            session.flush()
            save_transcript(session, ctx.job_id, TranscriptKind.CLEAN, clean, cut_list_id=row.id)
            job = session.get(Job, ctx.job_id)
            for flag in boundaries.flags:
                job.add_flag(flag)
            version = row.version

        original, final = cut_list.original_duration, cut_list.final_duration
        removed: dict[str, float] = {}
        for segment in cut_list.removed:
            removed[segment.reason] = removed.get(segment.reason, 0.0) + segment.duration
        total_seconds = time.monotonic() - started
        loudness = (result.loudness.get("result") or {}).get("output_i")
        return StageResult(
            metrics={
                "original_seconds": round(original, 3),
                "final_seconds": round(final, 3),
                "removed_seconds": round(original - final, 3),
                "removed_by_reason": {k: round(v, 3) for k, v in removed.items()},
                "cut_list_version": version,
                "parts": len(sources),
                "lesson_start": boundaries.start and boundaries.start.method,
                "lesson_end": boundaries.end and boundaries.end.method,
                "encoder": result.encoder,
                "framing": zooms,  # per part: the zoom, or None (whole picture)
                "size": f"{result.width}x{result.height}",
                "loudness_lufs": float(loudness) if loudness is not None else None,
                "seconds": {k: round(v, 1) for k, v in timings.items()},
                "realtime_factor": round(total_seconds / original, 4) if original else None,
                "llm": llm.usage() if llm is not None else None,
            },
            message=(
                f"{_clock(original)} cut to {_clock(final)} "
                f"({original - final:.0f} s removed) in {total_seconds:.0f} s"
            ),
        )


def _write_summary(path: Path, data: dict[str, Any]) -> None:
    partial = path.with_name(path.name + ".part")
    partial.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), "utf-8")
    os.replace(partial, path)
