"""FFmpeg building blocks (T-201): a runner with progress and cancellation, ``probe``,
``validate`` and ``extract_audio`` (16 kHz mono WAV for speech recognition)."""

import json
import os
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from reel.media.ffmpeg_check import FfmpegError

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
Progress = Callable[[float, float | None], None]  # seconds done, fraction done (or None)
CancelHook = Callable[[Callable[[], None]], None]  # e.g. StageContext.on_cancel


class FfmpegCancelled(FfmpegError):
    """FFmpeg was stopped because the stage had to stop."""


class InvalidMedia(Exception):
    """The file is not a usable recording; the message says why."""


def progress_seconds(line: str) -> float | None:
    """Seconds done from one ``-progress`` line (``out_time_ms`` is in µs too)."""
    key, _, value = line.strip().partition("=")
    if key in ("out_time_us", "out_time_ms") and value.lstrip("-").isdigit():
        return max(int(value), 0) / 1_000_000
    return None


def run_ffmpeg(
    ffmpeg: Path | str,
    args: Sequence[Any],
    *,
    duration: float | None = None,
    on_progress: Progress | None = None,
    on_cancel: CancelHook | None = None,
    on_stderr: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Run FFmpeg; return its last progress values (e.g. ``frame``). Raises
    ``FfmpegCancelled`` when stopped through ``on_cancel``, ``FfmpegError`` on failure.
    ``on_stderr`` gets every log line (e.g. to read a filter's measurements)."""
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-nostats", "-progress", "pipe:1"]
    command += [str(arg) for arg in args]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
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
    errors: deque[str] = deque(maxlen=20)

    def read_log() -> None:
        for raw in process.stderr:
            line = raw.rstrip()
            errors.append(line)
            if on_stderr is not None:
                on_stderr(line)

    reader = threading.Thread(target=read_log, daemon=True)
    reader.start()
    values: dict[str, str] = {}
    with process:  # closes the pipes and waits for the process, also on errors
        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            values[key] = value
            seconds = progress_seconds(line)
            if seconds is not None and on_progress is not None:
                on_progress(seconds, min(seconds / duration, 1.0) if duration else None)
        reader.join(5)
    if cancelled.is_set():
        raise FfmpegCancelled("FFmpeg was stopped")
    if process.returncode != 0:
        detail = " | ".join(line for line in errors if line) or "no error output"
        raise FfmpegError(f"FFmpeg failed (exit code {process.returncode}): {detail}")
    return values


@dataclass(frozen=True)
class MediaInfo:
    """What ffprobe reports about a recording (stored as ``Recording.media_info``)."""

    path: str
    size_bytes: int
    container: str
    duration_seconds: float | None
    video_codec: str | None
    width: int | None
    height: int | None
    fps: float | None
    audio_codec: str | None
    sample_rate: int | None
    channels: int | None
    creation_time: datetime | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["creation_time"] = self.creation_time.isoformat() if self.creation_time else None
        return data


def _rate(text: str | None) -> float | None:
    numerator, _, denominator = (text or "").partition("/")
    try:
        value = float(numerator) / float(denominator or 1)
    except ValueError, ZeroDivisionError:
        return None
    return round(value, 3) if value > 0 else None


def _number(value: Any, kind: type) -> Any:
    try:
        return kind(value) if value not in (None, "", "N/A") else None
    except TypeError, ValueError:
        return None


def probe(ffprobe: Path | str, path: Path | str) -> MediaInfo:
    """Read duration, streams, codecs, picture size, frame rate and creation time."""
    command = [str(ffprobe), "-v", "error", "-print_format", "json"]
    command += ["-show_format", "-show_streams", str(path)]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FfmpegError(f"cannot run {ffprobe}: {exc}") from exc
    if result.returncode != 0:
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        reason = lines[-1] if lines else f"ffprobe exit code {result.returncode}"
        raise InvalidMedia(f"not a readable media file: {reason}")
    data = json.loads(result.stdout or "{}")
    form = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    created = (form.get("tags") or {}).get("creation_time")
    try:
        creation_time = datetime.fromisoformat(created) if created else None
    except ValueError:
        creation_time = None
    return MediaInfo(
        path=str(path),
        size_bytes=Path(path).stat().st_size,
        container=form.get("format_name", ""),
        duration_seconds=_number(form.get("duration"), float),
        video_codec=video.get("codec_name"),
        width=_number(video.get("width"), int),
        height=_number(video.get("height"), int),
        fps=_rate(video.get("avg_frame_rate")),
        audio_codec=audio.get("codec_name"),
        sample_rate=_number(audio.get("sample_rate"), int),
        channels=_number(audio.get("channels"), int),
        creation_time=creation_time,
    )


def validate(
    ffmpeg: Path | str,
    ffprobe: Path | str,
    path: Path | str,
    *,
    min_duration_seconds: float = 0.0,
    check_seconds: float = 3.0,
    on_cancel: CancelHook | None = None,
) -> MediaInfo:
    """Check that the file is a usable recording (FR-2), else raise ``InvalidMedia``.

    Video and audio must be present and the duration known and long enough. The first and
    the last ``check_seconds`` are decoded: a truncated or damaged file fails there. (A full
    decode of a 3-hour file would take too long.)
    """
    info = probe(ffprobe, path)
    if not info.video_codec:
        raise InvalidMedia("no video stream")
    if not info.audio_codec:
        raise InvalidMedia("no audio stream")
    if not info.duration_seconds:
        raise InvalidMedia("the duration is unknown")
    if info.duration_seconds < min_duration_seconds:
        raise InvalidMedia(
            f"too short: {info.duration_seconds:.1f} s (minimum {min_duration_seconds:.1f} s)"
        )
    end = max(info.duration_seconds - check_seconds, 0.0)
    for part, start in (("beginning", 0.0), ("end", end)):
        args = ["-v", "error", "-xerror", "-ss", f"{start:.3f}", "-t", f"{check_seconds:g}"]
        args += ["-i", path, "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"]
        try:
            values = run_ffmpeg(ffmpeg, args, on_cancel=on_cancel)
        except FfmpegCancelled:
            raise
        except FfmpegError as exc:
            raise InvalidMedia(
                f"the {part} cannot be decoded (damaged or truncated): {exc}"
            ) from exc
        if not _number(values.get("frame"), int):
            raise InvalidMedia(f"the {part} has no decodable video (truncated file?)")
    return info


def extract_audio(
    ffmpeg: Path | str,
    source: Path | str,
    target: Path | str,
    *,
    duration: float | None = None,
    on_progress: Progress | None = None,
    on_cancel: CancelHook | None = None,
) -> Path:
    """Write the first audio track as 16 kHz mono 16-bit WAV. The target appears only
    when complete (written as ``.part``, then renamed), so a re-run is always safe."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    args = ["-y", "-v", "error", "-i", source, "-map", "0:a:0", "-vn"]
    args += ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", partial]
    try:
        run_ffmpeg(ffmpeg, args, duration=duration, on_progress=on_progress, on_cancel=on_cancel)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, target)
    return target
