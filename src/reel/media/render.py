"""Render the cleaned video from the cut list (T-405; FR-10, FR-11, NFR-4, D-037).

One FFmpeg filter graph, written to a file so long cut lists do not hit
the Windows command-line limit: every part of the lesson is an input; each kept segment is
trimmed from its part (``trim``/``atrim``), gets 30 ms audio fades at its edges (no clicks at the
joins) and all segments are joined with ``concat``. Loudness is normalised in two passes:
the cleaned audio is measured first (``loudnorm`` print), then normalised linearly to the target.

Pictures taller than ``render.max_height`` (shorter side, so portrait works too) are scaled
down, never up (D-037). ``video_encoder = "auto"`` runs everything on the Intel GPU (Quick Sync
decode, ``scale_qsv``, ``h264_qsv``: about 3x faster than libx264 on the user's laptop) and falls
back to libx264 on the CPU when that fails or cannot be used (rotated or mixed-shape parts).

A part may have a crop (the board framing, D-058): it is cut out before the scaling, in the
same encode (``crop`` on the CPU, ``vpp_qsv`` with ``cx/cy/cw/ch`` on the GPU).
"""

import json
import logging
import math
import os
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reel.cleaning.cutlist import CutList
from reel.cleaning.framing import Crop
from reel.config.schema import AudioSettings, RenderSettings
from reel.media.ffmpeg import NO_WINDOW, CancelHook, FfmpegCancelled, FfmpegError, run_ffmpeg
from reel.media.ffmpeg_check import run_tool

log = logging.getLogger("reel.media")

SAMPLE_RATE = 48000
QSV_PRESET = "medium"
Progress = Callable[[float, float | None], None]


@dataclass(frozen=True)
class SourceVideo:
    path: Path
    width: int  # as displayed (rotation applied)
    height: int
    rotation: int
    has_audio: bool


@dataclass(frozen=True)
class RenderResult:
    path: Path
    duration: float  # of the cut list
    width: int
    height: int
    encoder: str
    loudness: dict[str, Any]
    seconds: float
    details: dict[str, Any] = field(default_factory=dict)


def probe_source(ffprobe: Path | str, path: Path | str) -> SourceVideo:
    path = Path(path)
    command = [
        str(ffprobe), "-v", "error",
        "-show_entries", "stream=codec_type,width,height:stream_side_data=rotation",
        "-of", "json", str(path),
    ]  # fmt: skip
    try:
        output = subprocess.check_output(
            command, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW
        )
        streams = json.loads(output).get("streams") or []
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise FfmpegError(f"cannot read {path.name}: {exc}") from exc
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None or not video.get("width"):
        raise FfmpegError(f"{path.name} has no video stream")
    rotation = int(next((d.get("rotation") for d in video.get("side_data_list", [])), 0) or 0)
    width, height = int(video["width"]), int(video["height"])
    if abs(rotation) % 180 == 90:
        width, height = height, width
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return SourceVideo(path, width, height, rotation, has_audio)


def output_size(width: int, height: int, max_height: int) -> tuple[int, int]:
    """Scale down so the shorter side is at most ``max_height``; never up; even numbers."""
    short = min(width, height)
    ratio = max_height / short if short > max_height else 1.0
    return max(2, int(width * ratio) // 2 * 2), max(2, int(height * ratio) // 2 * 2)


def _seconds(value: float) -> str:
    return f"{value:.6f}"


def build_graph(
    cut_list: CutList,
    sources: Sequence[SourceVideo],
    size: tuple[int, int],
    *,
    gpu: bool,
    fade_seconds: float,
    video: bool = True,
    loudnorm: str = "",
    crops: Sequence[Crop | None] | None = None,
) -> str:
    """The filter graph: trimmed segments of every part joined by ``concat``. Outputs ``[v]``
    (with ``video``) and ``[a]``. ``crops``: one per part, or None."""
    width, height = size
    crops = list(crops) if crops is not None else [None] * len(sources)
    lines: list[str] = []
    labels: list[str] = []
    for part, source in enumerate(sources):
        kept = [s for s in cut_list.kept if s.part == part]
        if not kept:
            continue
        offset = sum(p.duration for p in cut_list.parts[:part])
        names = [f"p{part}s{i}" for i in range(len(kept))]
        if video:
            crop = crops[part]
            if gpu and crop is not None:
                scale = (
                    f"vpp_qsv=cx={crop.x}:cy={crop.y}:cw={crop.width}:ch={crop.height}"
                    f":w={width}:h={height}"
                )
            elif gpu:
                scale = f"scale_qsv=w={width}:h={height}"
            else:
                cut = f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}," if crop else ""
                scale = (
                    f"{cut}scale={width}:{height}:force_original_aspect_ratio=decrease"
                    f":flags=bicubic,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                    "format=yuv420p"
                )
            split = f",split={len(kept)}" if len(kept) > 1 else ""
            lines.append(f"[{part}:v]{scale}{split}" + "".join(f"[{n}v]" for n in names))
        if source.has_audio:
            split = f"asplit={len(kept)}" if len(kept) > 1 else "anull"
            lines.append(f"[{part}:a]{split}" + "".join(f"[{n}a]" for n in names))
        for name, segment in zip(names, kept, strict=True):
            start, end = segment.start - offset, segment.end - offset
            length = end - start
            if video:
                lines.append(
                    f"[{name}v]trim=start={_seconds(start)}:end={_seconds(end)},"
                    f"setpts=PTS-STARTPTS[{name}V]"
                )
            if source.has_audio:
                audio = (
                    f"[{name}a]atrim=start={_seconds(start)}:end={_seconds(end)},"
                    "asetpts=PTS-STARTPTS,"
                )
            else:  # a part without sound: silence of the same length
                audio = f"anullsrc=r={SAMPLE_RATE}:cl=stereo,atrim=duration={_seconds(length)},"
            audio += f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
            if fade_seconds > 0 and length > 4 * fade_seconds:
                audio += (
                    f",afade=t=in:d={_seconds(fade_seconds)}"
                    f",afade=t=out:st={_seconds(length - fade_seconds)}:d={_seconds(fade_seconds)}"
                )
            lines.append(f"{audio}[{name}A]")
            labels.append(f"[{name}V][{name}A]" if video else f"[{name}A]")
    if not labels:
        raise FfmpegError("the cut list keeps nothing")
    joined = "[v][ca]" if video else "[ca]"
    lines.append(f"{''.join(labels)}concat=n={len(labels)}:v={int(video)}:a=1{joined}")
    lines.append(f"[ca]{loudnorm or 'anull'}[a]")
    return ";\n".join(lines) + "\n"


_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def _parse_loudnorm(log_lines: list[str]) -> dict[str, Any]:
    match = _LOUDNORM_JSON.search("\n".join(log_lines))
    if match is None:
        raise FfmpegError("FFmpeg did not report the loudness measurement")
    return json.loads(match.group(0))


def _target(audio: AudioSettings) -> str:
    return f"I={audio.loudness_target_lufs}:TP={audio.true_peak_db}:LRA={audio.loudness_range}"


def _inputs(sources: Sequence[SourceVideo], gpu: bool) -> list[str]:
    args: list[str] = []
    for source in sources:
        if gpu:
            args += ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]
        args += ["-i", str(source.path)]
    return args


def render(
    ffmpeg: Path | str,
    ffprobe: Path | str,
    sources: Sequence[Path | str],
    cut_list: CutList,
    target: Path | str,
    settings: RenderSettings,
    audio: AudioSettings,
    *,
    fade_ms: int = 30,
    work_dir: Path | str | None = None,
    crops: Sequence[Crop | None] | None = None,
    on_progress: Progress | None = None,
    on_cancel: CancelHook | None = None,
) -> RenderResult:
    """Write the cleaned, loudness-normalised video atomically (``.part``, then rename)."""
    started = time.monotonic()
    if len(sources) != len(cut_list.parts):
        raise FfmpegError(f"{len(sources)} files for a cut list of {len(cut_list.parts)} parts")
    if crops is not None and len(crops) != len(sources):
        raise FfmpegError(f"{len(crops)} crops for {len(sources)} parts")
    videos = [probe_source(ffprobe, path) for path in sources]
    size = output_size(videos[0].width, videos[0].height, settings.max_height)
    target = Path(target)
    work = Path(work_dir) if work_dir is not None else target.parent
    work.mkdir(parents=True, exist_ok=True)
    total = cut_list.final_duration
    fade = fade_ms / 1000

    def report(share: float, done: float | None = None) -> None:
        if on_progress is not None:
            on_progress(done if done is not None else share * total, min(max(share, 0.0), 1.0))

    # Pass 1: measure the loudness of the cleaned audio (audio only, fast).
    version = run_tool(Path(ffmpeg), "-version")
    major = re.match(r"ffmpeg version (\d+)\.", version)
    graph_option = "-filter_complex_script" if major and int(major[1]) < 7 else "-/filter_complex"
    measure_graph = work / "render.measure.graph"
    measure_graph.write_text(
        build_graph(
            cut_list, videos, size, gpu=False, fade_seconds=fade, video=False,
            loudnorm=f"loudnorm={_target(audio)}:print_format=json",
        ),
        encoding="utf-8",
    )  # fmt: skip
    measure_log: list[str] = []
    run_ffmpeg(
        ffmpeg,
        ["-v", "info", *(a for v in videos for a in ("-i", str(v.path))),
         graph_option, measure_graph, "-map", "[a]", "-f", "null", "-"],
        duration=total,
        on_progress=lambda done, share: report(0.1 * (share or 0)),
        on_cancel=on_cancel,
        on_stderr=measure_log.append,
    )  # fmt: skip
    measured = _parse_loudnorm(measure_log)
    loudness: dict[str, Any] = {"measured": measured, "target": audio.loudness_target_lufs}
    input_i = float(measured.get("input_i", "-inf"))
    if math.isfinite(input_i) and input_i > -70:
        loudnorm = (
            f"loudnorm={_target(audio)}:measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}"
            f":linear=true:print_format=json,aresample={SAMPLE_RATE}"
        )
    else:
        log.warning("the cleaned audio is (almost) silent; loudness is not normalised")
        loudnorm, loudness["skipped"] = f"aresample={SAMPLE_RATE}", "silent"

    # Pass 2: the video. GPU first when allowed and possible, else the CPU.
    gpu_possible = (
        all(v.rotation == 0 for v in videos)
        and len({round(v.width / v.height, 3) for v in videos}) == 1
    )
    if settings.video_encoder == "h264_qsv" and not gpu_possible:
        raise FfmpegError("Quick Sync cannot render rotated or mixed-shape parts; use libx264")
    plans = {"auto": [True, False] if gpu_possible else [False], "h264_qsv": [True]}
    partial = target.with_name(target.name + ".part")
    errors = []
    for gpu in plans.get(settings.video_encoder, [False]):
        graph = work / "render.graph"
        graph.write_text(
            build_graph(
                cut_list, videos, size, gpu=gpu, fade_seconds=fade, loudnorm=loudnorm, crops=crops
            ),  # fmt: skip
            encoding="utf-8",
        )
        if gpu:
            codec = ["-c:v", "h264_qsv", "-preset", QSV_PRESET,
                     "-global_quality", str(settings.qsv_quality)]  # fmt: skip
        else:
            codec = ["-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
                     "-pix_fmt", "yuv420p"]  # fmt: skip
        render_log: list[str] = []
        try:
            run_ffmpeg(
                ffmpeg,
                ["-y", "-v", "info", *_inputs(videos, gpu), graph_option, graph,
                 "-map", "[v]", "-map", "[a]", *codec,
                 "-c:a", "aac", "-b:a", settings.audio_bitrate, "-ar", str(SAMPLE_RATE),
                 "-movflags", "+faststart", "-f", "mp4", partial],
                duration=total,
                on_progress=lambda done, share: report(0.1 + 0.9 * (share or 0), done),
                on_cancel=on_cancel,
                on_stderr=render_log.append,
            )  # fmt: skip
        except FfmpegCancelled:
            partial.unlink(missing_ok=True)
            raise
        except FfmpegError as exc:
            partial.unlink(missing_ok=True)
            errors.append(str(exc))
            if not gpu or settings.video_encoder != "auto":
                raise
            log.warning("Quick Sync rendering failed, rendering on the CPU instead: %s", exc)
            continue
        encoder = "h264_qsv" if gpu else "libx264"
        break
    if loudness.get("skipped") is None:
        try:  # the second pass reports the result too (output_i, normalization_type)
            loudness["result"] = _parse_loudnorm(render_log)
        except FfmpegError:
            loudness["result"] = None
    os.replace(partial, target)
    seconds = round(time.monotonic() - started, 1)
    log.info(
        "rendered %s: %.1f s of video, %dx%d, %s, %.0f s",
        target.name, total, size[0], size[1], encoder, seconds,
    )  # fmt: skip
    details = {
        "gpu_errors": errors,
        "parts": [str(v.path.name) for v in videos],
        "crops": [None if c is None else [c.x, c.y, c.width, c.height] for c in crops or []],
    }
    return RenderResult(target, total, size[0], size[1], encoder, loudness, seconds, details)
