"""Is anybody at the board? Long empty stretches are shortened (T-403; FR-8, D-040).

The user's rule: when nobody is at the board for more than ``min_empty_seconds`` (10 s), the
stretch is shortened to ``keep_seconds`` (5 s, half kept at each side), whether or not someone
is heard. This removes the break (usually between the parts of a lesson, but not always) and
every other empty stretch.

About one picture per second is checked by a small person detector (YOLOX, Apache-2.0, ONNX,
run by onnxruntime on the CPU). When the video's keyframes are dense enough (iPhone: every
0.83 s) only keyframes are decoded, on the GPU when possible (d3d11va).

Conservative by design: a stretch counts as empty only between two samples that both saw
nobody, a gap in the samples (e.g. a damaged part of the file) breaks it, and anything that
looks like a person keeps the picture.
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import numpy as np

from reel.cleaning.framing import FramingResult, WorkCollector
from reel.cleaning.silence import Interval, Removal
from reel.config.schema import EmptyBoardSettings, FramingSettings
from reel.media.ffmpeg import NO_WINDOW, CancelHook, FfmpegCancelled, FfmpegError

log = logging.getLogger("reel.cleaning")

INPUT_SIZE = 416  # both YOLOX models take 416 x 416 pictures
PAD_VALUE = 114  # YOLOX letterbox colour
PERSON = 0  # COCO class index
MASK_SCORE = 0.25  # people masked out for the framing (D-058): also small and unsure ones
MAX_PEOPLE = 50


class ModelFile(NamedTuple):
    url: str
    sha256: str


_RELEASE = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0"
MODELS = {
    "yolox_tiny": ModelFile(
        f"{_RELEASE}/yolox_tiny.onnx",
        "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7",
    ),
    "yolox_nano": ModelFile(
        f"{_RELEASE}/yolox_nano.onnx",
        "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d",
    ),
}


class ModelMissing(FileNotFoundError):
    """The person detector is not installed."""


def model_path(models_dir: Path, name: str) -> Path:
    return Path(models_dir) / "person" / f"{name}.onnx"


@dataclass(frozen=True)
class Sample:
    seconds: float
    score: float  # best person score among figures that are tall enough (0 = nobody)


class Detector(Protocol):
    def __call__(self, frame: np.ndarray, content_height: int) -> float:
        """Best person score in a ``INPUT_SIZE`` x ``INPUT_SIZE`` BGR picture whose real content
        is the top-left part (``content_height`` rows)."""
        ...


class PersonDetector:
    """YOLOX (ONNX) on the CPU; only the "person" class and the figure height are used."""

    def __init__(self, model: Path, *, min_height: float, threads: int = 0) -> None:
        import onnxruntime as ort

        if not Path(model).is_file():
            raise ModelMissing(
                f"person detector {model} is missing; install it with "
                r"'.venv\Scripts\python scripts\setup_person_model.py'"
            )
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads or max(1, (os.cpu_count() or 4) - 2)
        options.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(model), options, providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name
        self._min_height = min_height
        grids, strides = [], []
        for stride in (8, 16, 32):
            size = INPUT_SIZE // stride
            ys, xs = np.meshgrid(np.arange(size), np.arange(size), indexing="ij")
            grids.append(np.stack((xs, ys), axis=-1).reshape(-1, 2))
            strides.append(np.full((size * size, 1), stride))
        self._grids = np.concatenate(grids).astype(np.float32)
        self._strides = np.concatenate(strides).astype(np.float32)
        self.last_people: list[tuple[float, float, float, float]] = []

    def __call__(self, frame: np.ndarray, content_height: int) -> float:
        """Also keeps the boxes of everybody seen in ``last_people`` (x0, y0, x1, y1 pixels)."""
        blob = np.ascontiguousarray(frame.transpose(2, 0, 1)[None], dtype=np.float32)
        out = self._session.run(None, {self._input: blob})[0][0]
        scores = out[:, 4] * out[:, 5 + PERSON]  # objectness x class score (already sigmoid)
        sizes = np.exp(out[:, 2:4]) * self._strides
        heights = sizes[:, 1]
        seen = np.flatnonzero(scores >= MASK_SCORE)
        seen = seen[np.argsort(scores[seen])[::-1][:MAX_PEOPLE]]
        centres = (out[seen, :2] + self._grids[seen]) * self._strides[seen]
        half = sizes[seen] / 2
        self.last_people = [
            (float(x - w), float(y - h), float(x + w), float(y + h))
            for (x, y), (w, h) in zip(centres, half, strict=True)
        ]
        tall = heights >= self._min_height * content_height
        return float(scores[tall].max()) if tall.any() else 0.0


@dataclass(frozen=True)
class BoardAnalysis:
    samples: tuple[Sample, ...]
    empty: tuple[Interval, ...]  # stretches with nobody, longer than min_empty_seconds
    removals: tuple[Removal, ...]  # what to cut (each stretch minus keep_seconds)
    mode: str  # "keyframes" or "fps", plus "+d3d11va" when decoded on the GPU
    seconds: float  # processing time
    details: dict[str, Any] = field(default_factory=dict)
    framing: FramingResult | None = None  # D-058, when asked for

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["samples"] = [[s.seconds, round(s.score, 3)] for s in self.samples]
        data["framing"] = self.framing.to_dict() if self.framing is not None else None
        return data


def empty_stretches(
    samples: Sequence[Sample],
    settings: EmptyBoardSettings,
    *,
    duration: float | None = None,
) -> tuple[list[Interval], list[Removal]]:
    """Runs of samples that saw nobody, longer than ``min_empty_seconds``, and the part of each
    that is cut so that ``keep_seconds`` remain (half at each side)."""
    max_gap = 3 * settings.sample_seconds
    runs: list[tuple[float, float]] = []
    first = previous = None
    for sample in sorted(samples, key=lambda s: s.seconds):
        present = sample.score >= settings.person_score
        gap = previous is not None and sample.seconds - previous > max_gap
        if first is not None and (present or gap):
            runs.append((first, previous))
            first = None
        if not present and first is None:
            first = sample.seconds
        previous = sample.seconds
    if first is not None:
        # Nobody until the end: the last sample is at most one sample step before it.
        last = previous
        if duration is not None and duration - previous <= max_gap:
            last = duration
        runs.append((first, last))
    empty, removals = [], []
    half = settings.keep_seconds / 2
    for start, end in runs:
        if end - start <= settings.min_empty_seconds:
            continue
        empty.append(Interval(start, end))
        reason = f"nobody at the board for {end - start:.0f} s (D-040)"
        removals.append(Removal(start + half, end - half, reason))
    return empty, removals


_SHOWINFO = re.compile(r"\[Parsed_showinfo_\d+ @ [^\]]+\] n:\s*(\d+)\s.*?pts_time:\s*(\S+)")


def _probe_video(ffprobe: Path | str, video: Path) -> tuple[int, int, float | None, float | None]:
    """Displayed width and height (rotation applied), duration, median keyframe interval."""
    command = [
        str(ffprobe), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:stream_side_data=rotation:format=duration",
        "-of", "json", str(video),
    ]  # fmt: skip
    packets = [
        str(ffprobe), "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+60",
        "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(video),
    ]  # fmt: skip
    try:
        info = json.loads(_check_output(command))
        keys = _check_output(packets)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise FfmpegError(f"cannot read the video stream of {video.name}: {exc}") from exc
    streams = info.get("streams") or []
    if not streams or not streams[0].get("width"):
        raise FfmpegError(f"{video.name} has no video stream")
    stream = streams[0]
    width, height = int(stream["width"]), int(stream["height"])
    rotation = next((d.get("rotation") for d in stream.get("side_data_list", [])), 0) or 0
    if abs(int(rotation)) % 180 == 90:
        width, height = height, width
    duration = info.get("format", {}).get("duration")
    times = []
    for line in keys.splitlines():
        pts, _, flags = line.partition(",")
        if "K" in flags and pts not in ("", "N/A"):
            times.append(float(pts))
    times.sort()
    steps = sorted(b - a for a, b in zip(times, times[1:], strict=False) if b > a)
    interval = steps[len(steps) // 2] if steps else None
    return width, height, float(duration) if duration else None, interval


def _check_output(command: list[str]) -> str:
    return subprocess.check_output(
        command, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW
    )


def _frames(
    ffmpeg: Path | str,
    video: Path,
    *,
    size: tuple[int, int],
    keyframes: bool,
    sample_seconds: float,
    hwaccel: str | None,
    on_frame: Callable[[int, np.ndarray], None],
    on_cancel: CancelHook | None,
) -> list[float]:
    """Decode the pictures, give each (index, BGR array) to ``on_frame``; return their times."""
    width, height = size
    chain = [] if keyframes else [f"fps=1/{sample_seconds}"]
    chain += ["showinfo", f"scale={width}:{height}:flags=area"]
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-nostats", "-v", "info"]
    if hwaccel:
        command += ["-hwaccel", hwaccel]
    if keyframes:
        command += ["-skip_frame", "nokey"]
    command += ["-i", str(video), "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", ",".join(chain)]
    command += ["-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW
        )
    except OSError as exc:
        raise FfmpegError(f"cannot run {ffmpeg}: {exc}") from exc
    cancelled = threading.Event()

    def kill() -> None:
        cancelled.set()
        with suppress(OSError):
            process.kill()

    if on_cancel is not None:
        on_cancel(kill)
    times: dict[int, float] = {}
    errors: list[str] = []

    def read_log() -> None:
        for raw in process.stderr:
            line = raw.decode("utf-8", "replace")
            match = _SHOWINFO.search(line)
            if match:
                with suppress(ValueError):
                    times[int(match.group(1))] = float(match.group(2))
            elif "rror" in line:
                errors.append(line.strip())

    reader = threading.Thread(target=read_log, daemon=True)
    reader.start()
    frame_bytes = width * height * 3
    count = 0
    with process:
        while True:
            data = process.stdout.read(frame_bytes)
            if len(data) < frame_bytes:
                break
            on_frame(count, np.frombuffer(data, np.uint8).reshape(height, width, 3))
            count += 1
        if cancelled.is_set():
            process.kill()
        reader.join(10)
    if cancelled.is_set():
        raise FfmpegCancelled("FFmpeg was stopped")
    if process.returncode != 0:
        detail = " | ".join(errors[-5:]) or "no error output"
        raise FfmpegError(f"FFmpeg failed (exit code {process.returncode}): {detail}")
    if sorted(times) != list(range(count)):
        raise FfmpegError(f"picture times do not match: {count} pictures, {len(times)} times")
    return [times[i] for i in range(count)]


def analyze_board(
    ffmpeg: Path | str,
    ffprobe: Path | str,
    video: Path | str,
    settings: EmptyBoardSettings,
    *,
    models_dir: Path | None = None,
    detector: Detector | None = None,
    framing: FramingSettings | None = None,
    on_progress: Callable[[float, float | None], None] | None = None,
    on_cancel: CancelHook | None = None,
) -> BoardAnalysis:
    """Sample the video, find who is at the board, return the empty stretches to shorten.
    With ``framing``, the same pictures also give the work box (D-058)."""
    started = time.monotonic()
    video = Path(video)
    width, height, duration, key_interval = _probe_video(ffprobe, video)
    if detector is None:
        if models_dir is None:
            raise ValueError("models_dir or detector is needed")
        detector = PersonDetector(
            model_path(models_dir, settings.model), min_height=settings.min_person_height
        )
    ratio = min(INPUT_SIZE / width, INPUT_SIZE / height)
    size = (max(2, round(width * ratio)), max(2, round(height * ratio)))
    keyframes = key_interval is not None and key_interval <= settings.sample_seconds * 1.5
    canvas = np.full((INPUT_SIZE, INPUT_SIZE, 3), PAD_VALUE, np.uint8)
    scores: list[float] = []
    step = key_interval if keyframes and key_interval else settings.sample_seconds
    collector: list[WorkCollector] = []

    def on_frame(index: int, frame: np.ndarray) -> None:
        canvas[: size[1], : size[0]] = frame
        scores.append(detector(canvas, size[1]))
        if collector:
            collector[0].add(frame, getattr(detector, "last_people", ()))
        if on_progress is not None and duration and index % 30 == 0:
            done = min(
                len(scores) * (key_interval if keyframes else settings.sample_seconds), duration
            )
            on_progress(done, done / duration)

    accels = [None]
    if settings.hwaccel == "d3d11va" or (settings.hwaccel == "auto" and os.name == "nt"):
        accels = ["d3d11va", None]
    for accel in accels:
        scores.clear()
        collector[:] = [WorkCollector(framing, size, step)] if framing is not None else []
        try:
            times = _frames(
                ffmpeg,
                video,
                size=size,
                keyframes=keyframes,
                sample_seconds=settings.sample_seconds,
                hwaccel=accel,
                on_frame=on_frame,
                on_cancel=on_cancel,
            )
            break
        except FfmpegCancelled:
            raise
        except FfmpegError as exc:
            if accel is None or settings.hwaccel != "auto":
                raise
            log.warning("GPU decoding failed, decoding on the CPU instead: %s", exc)
    samples = tuple(Sample(round(t, 3), s) for t, s in zip(times, scores, strict=True))
    if not samples:
        raise FfmpegError(f"no pictures could be read from {video.name}")
    empty, removals = empty_stretches(samples, settings, duration=duration)
    mode = ("keyframes" if keyframes else "fps") + (f"+{accel}" if accel else "")
    seconds = round(time.monotonic() - started, 1)
    present = sum(s.score >= settings.person_score for s in samples)
    log.info(
        "board check of %s: %d pictures (%s), somebody at the board in %d, %d empty stretches "
        "to shorten (%.0f s removed), %.0f s",
        video.name, len(samples), mode, present, len(removals),
        sum(r.end - r.start for r in removals), seconds,
    )  # fmt: skip
    details = {
        "duration": duration,
        "keyframe_interval": key_interval,
        "present_samples": present,
        "picture_size": list(size),
        "source_size": [width, height],
        "model": settings.model,
    }
    framed = collector[0].finish() if collector else None
    return BoardAnalysis(samples, tuple(empty), tuple(removals), mode, seconds, details, framed)
