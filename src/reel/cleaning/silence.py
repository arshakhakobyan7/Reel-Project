"""Detect removable silences in one audio/video pass (T-401, D-014).

Only the first audio and video streams are analysed. Audio-only media has no freeze
intervals: absence of a picture is not evidence of a static picture. Its silences are
removable only when ``require_static_video`` is false. Times are source seconds.
"""

import logging
import math
import re
import subprocess
import threading
from collections import deque
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from reel.config.schema import SilenceSettings
from reel.media.ffmpeg import (
    NO_WINDOW,
    CancelHook,
    FfmpegCancelled,
    FfmpegError,
    InvalidMedia,
    Progress,
    probe,
    progress_seconds,
)

log = logging.getLogger("reel.cleaning")

_EVENT = re.compile(
    r"\b(silence|freeze)_(start|end):\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)


@dataclass(frozen=True)
class Interval:
    """A nonempty interval on the source timeline, in seconds."""

    start: float
    end: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.start) and math.isfinite(self.end)):
            raise ValueError("interval times must be finite")
        if not 0 <= self.start < self.end:
            raise ValueError("interval times must satisfy 0 <= start < end")


@dataclass(frozen=True)
class Removal(Interval):
    """A removable interval and its human-readable evidence."""

    reason: str


@dataclass(frozen=True)
class SilenceAnalysis:
    """Raw detections and ordered, padded removals for the cut planner."""

    silences: list[Interval]
    freezes: list[Interval]
    removals: list[Removal]


def _merge(intervals: Iterable[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for interval in sorted(intervals, key=lambda item: (item.start, item.end)):
        if merged and interval.start <= merged[-1].end:
            previous = merged[-1]
            merged[-1] = Interval(previous.start, max(previous.end, interval.end))
        else:
            merged.append(interval)
    return merged


def parse_detection_log(
    output: str | Iterable[str], duration: float
) -> tuple[list[Interval], list[Interval]]:
    """Parse silence/freeze events; clip to duration and close unfinished intervals.

    Unmatched ends and empty intervals are ignored. Duplicate starts keep the first
    start. Duration-only messages and unrelated diagnostics are not detection events.
    """
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("media duration must be finite and nonnegative")
    opened: dict[str, float] = {}
    found: dict[str, list[Interval]] = {"silence": [], "freeze": []}
    lines = output.splitlines() if isinstance(output, str) else output
    for line in lines:
        for match in _EVENT.finditer(line):
            kind, event, value = match.groups()
            seconds = float(value)
            if not math.isfinite(seconds):
                continue
            seconds = min(max(seconds, 0.0), duration)
            if event == "start":
                opened.setdefault(kind, seconds)
            else:
                start = opened.pop(kind, None)
                if start is not None and seconds > start:
                    found[kind].append(Interval(start, seconds))
    for kind, start in opened.items():
        if duration > start:
            found[kind].append(Interval(start, duration))
    return _merge(found["silence"]), _merge(found["freeze"])


def intersect_intervals(left: Sequence[Interval], right: Sequence[Interval]) -> list[Interval]:
    """Intersect interval unions without changing either input."""
    a, b = _merge(left), _merge(right)
    result: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        start, end = max(a[i].start, b[j].start), min(a[i].end, b[j].end)
        if start < end:
            result.append(Interval(start, end))
        if a[i].end <= b[j].end:
            i += 1
        else:
            j += 1
    return result


def removable_silences(
    silences: Sequence[Interval], freezes: Sequence[Interval], settings: SilenceSettings
) -> list[Removal]:
    """Require a long enough overlap, then leave configured silence on both sides."""
    if settings.require_static_video:
        candidates = intersect_intervals(silences, freezes)
        reason = "silence over a static picture"
    else:
        candidates = _merge(silences)
        reason = "silence"
    return [
        Removal(item.start + settings.keep_seconds, item.end - settings.keep_seconds, reason)
        for item in candidates
        if item.end - item.start >= settings.min_silence_seconds
    ]


def _run_analysis(
    ffmpeg: Path | str,
    args: Sequence[str],
    *,
    duration: float,
    on_progress: Progress | None = None,
    on_cancel: CancelHook | None = None,
) -> list[str]:
    """Run FFmpeg with the shared runner's callbacks while retaining detector events."""
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-nostats", "-progress", "pipe:1"]
    try:
        process = subprocess.Popen(
            [*command, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
    except OSError as exc:
        raise FfmpegError(f"cannot run {ffmpeg}: {exc}") from exc
    cancelled, finished = threading.Event(), threading.Event()
    events: list[str] = []
    errors: deque[str] = deque(maxlen=20)

    def kill() -> None:
        if not finished.is_set():
            cancelled.set()
            with suppress(OSError):
                process.kill()

    def read_errors() -> None:
        for line in process.stderr:
            errors.append(line.rstrip())
            if _EVENT.search(line):
                events.append(line)

    with process:
        reader = threading.Thread(target=read_errors, daemon=True)
        reader.start()
        try:
            if on_cancel is not None:
                on_cancel(kill)
            for line in process.stdout:
                seconds = progress_seconds(line)
                if seconds is not None and on_progress is not None:
                    on_progress(seconds, min(seconds / duration, 1.0))
            process.wait()
        finally:
            if process.poll() is None:
                with suppress(OSError):
                    process.kill()
            process.wait()
            reader.join()
            finished.set()
    if cancelled.is_set():
        raise FfmpegCancelled("FFmpeg was stopped")
    if process.returncode != 0:
        detail = " | ".join(line for line in errors if line) or "no error output"
        raise FfmpegError(f"FFmpeg failed (exit code {process.returncode}): {detail}")
    return events


def analyze_silence(
    ffmpeg: Path | str,
    ffprobe: Path | str,
    source: Path | str,
    settings: SilenceSettings,
    *,
    on_progress: Progress | None = None,
    on_cancel: CancelHook | None = None,
) -> SilenceAnalysis:
    """Probe media, then analyse audio and a 160-pixel, 5-fps picture in one pass.

    ``on_progress`` receives seconds and a fraction; ``on_cancel`` registers a kill
    callback, just like ``run_ffmpeg``. No pipeline state or media files are written.
    """
    info = probe(ffprobe, source)
    if not info.audio_codec and not info.video_codec:
        return SilenceAnalysis([], [], [])
    duration = info.duration_seconds
    if duration is None or not math.isfinite(duration) or duration <= 0:
        if not info.audio_codec:
            # No silence is possible, and an unknown end cannot close freeze intervals.
            return SilenceAnalysis([], [], [])
        raise InvalidMedia("silence analysis needs a known positive media duration")
    args = ["-v", "info", "-i", str(source)]
    minimum = str(settings.min_silence_seconds)
    if info.audio_codec:
        args += [
            "-map",
            "0:a:0",
            "-af",
            f"silencedetect=noise={settings.noise_db}dB:d={minimum}",
        ]
    if info.video_codec:
        args += [
            "-map",
            "0:v:0",
            "-vf",
            f"fps=5,scale=160:-2,freezedetect=n={settings.freeze_noise}:d={minimum}",
        ]
    args += ["-sn", "-dn", "-f", "null", "-"]
    events = _run_analysis(
        ffmpeg, args, duration=duration, on_progress=on_progress, on_cancel=on_cancel
    )
    silences, freezes = parse_detection_log(events, duration)
    removals = removable_silences(silences, freezes, settings)
    log.debug(
        "Silence analysis: %d silence(s), %d freeze(s), %d removal(s)",
        len(silences),
        len(freezes),
        len(removals),
    )
    return SilenceAnalysis(silences, freezes, removals)
