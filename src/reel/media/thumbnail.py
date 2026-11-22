"""Representative JPEG thumbnails with literal text and checked font coverage (T-503)."""

import ctypes
import logging
import math
import os
import re
import struct
import tempfile
import textwrap
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reel.media.ffmpeg import CancelHook, FfmpegCancelled, FfmpegError, probe, run_ffmpeg

log = logging.getLogger("reel.media")
FONT_DIR = Path(__file__).resolve().parents[3] / "assets" / "fonts"
DEFAULT_FONT = FONT_DIR / "DejaVuSans-Bold.ttf"
REGULAR_FONT = FONT_DIR / "DejaVuSans.ttf"
TEXT_WIDTH = 1280 - 2 * 56
WIDTH_SAFETY = 16
MAX_JPEG_BYTES = 2_000_000
_JPEG_QUALITIES = (2, 4, 6, 8, 12, 16, 20, 25, 31)  # Higher q means lower JPEG quality.
_PTS = re.compile(r"\bpts_time:([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)")


class ThumbnailError(FfmpegError):
    """The input/font/output cannot produce a valid thumbnail."""


class MissingGlyphs(ThumbnailError):
    """Refuse text that would render as missing-glyph boxes."""

    def __init__(self, characters: tuple[str, ...]) -> None:
        self.characters = characters
        details = ", ".join(f"{char!r} (U+{ord(char):04X})" for char in characters)
        super().__init__("Font cannot draw: " + details)


@dataclass(frozen=True)
class ThumbnailResult:
    path: Path
    size_bytes: int
    frame_time: float
    quality: int


def _tables(data: bytes) -> dict[bytes, bytes]:
    if len(data) < 12 or data[:4] not in {b"\x00\x01\x00\x00", b"OTTO", b"true"}:
        raise ValueError("invalid sfnt header")
    count = struct.unpack_from(">H", data, 4)[0]
    if 12 + 16 * count > len(data):
        raise ValueError("truncated sfnt directory")
    tables = {}
    for index in range(count):
        tag, _, offset, size = struct.unpack_from(">4sIII", data, 12 + index * 16)
        if offset + size > len(data) or tag in tables:
            raise ValueError("invalid sfnt table bounds")
        tables[tag] = data[offset : offset + size]
    return tables


def _format4(data: bytes) -> dict[int, int]:
    count2 = struct.unpack_from(">H", data, 6)[0]
    count = count2 // 2
    if not count or count2 % 2 or len(data) < 16 + 8 * count:
        raise ValueError("invalid cmap format 4 segments")
    starts, deltas, offsets = 16 + 2 * count, 16 + 4 * count, 16 + 6 * count
    result, previous = {}, -1
    for index in range(count):
        end = struct.unpack_from(">H", data, 14 + 2 * index)[0]
        start = struct.unpack_from(">H", data, starts + 2 * index)[0]
        delta = struct.unpack_from(">h", data, deltas + 2 * index)[0]
        offset = struct.unpack_from(">H", data, offsets + 2 * index)[0]
        if start > end or start <= previous:
            raise ValueError("unordered cmap segments")
        previous = end
        for codepoint in range(start, end + 1):
            if offset:
                address = offsets + 2 * index + offset + 2 * (codepoint - start)
                if address < 16 + 8 * count or address + 2 > len(data):
                    raise ValueError("invalid cmap glyph offset")
                glyph = struct.unpack_from(">H", data, address)[0]
                glyph = (glyph + delta) & 0xFFFF if glyph else 0
            else:
                glyph = (codepoint + delta) & 0xFFFF
            if glyph:
                result[codepoint] = glyph
    return result


def _format12(data: bytes) -> dict[int, int]:
    count = struct.unpack_from(">I", data, 12)[0]
    if 16 + 12 * count > len(data):
        raise ValueError("truncated cmap format 12")
    result, previous = {}, -1
    for index in range(count):
        start, end, first = struct.unpack_from(">III", data, 16 + 12 * index)
        if start > end or start <= previous or end > 0x10FFFF or first + end - start > 0xFFFF:
            raise ValueError("invalid cmap format 12 group")
        previous = end
        for codepoint in range(start, end + 1):
            glyph = first + codepoint - start
            if glyph:
                result[codepoint] = glyph
    return result


def _cmap(tables: dict[bytes, bytes]) -> dict[int, int]:
    data = tables[b"cmap"]
    version, count = struct.unpack_from(">HH", data)
    if version != 0 or 4 + 8 * count > len(data):
        raise ValueError("invalid cmap header")
    choices = []
    for index in range(count):
        platform, encoding, offset = struct.unpack_from(">HHI", data, 4 + 8 * index)
        if platform != 0 and not (platform == 3 and encoding in {1, 10}):
            continue
        kind = struct.unpack_from(">H", data, offset)[0]
        if kind not in {4, 12}:
            continue
        size = struct.unpack_from(
            ">H" if kind == 4 else ">I", data, offset + (2 if kind == 4 else 4)
        )[0]
        if size < (16 if kind == 12 else 24) or offset + size > len(data):
            raise ValueError("invalid cmap subtable bounds")
        choices.append((kind, data[offset : offset + size]))
    if not choices:
        raise ValueError("no Unicode cmap format 4 or 12")
    kind, subtable = max(choices, key=lambda item: item[0])
    return _format12(subtable) if kind == 12 else _format4(subtable)


def _font(font: Path | str) -> tuple[dict[bytes, bytes], dict[int, int]]:
    try:
        tables = _tables(Path(font).read_bytes())
        return tables, _cmap(tables)
    except OSError, ValueError, KeyError, struct.error, IndexError:
        raise ThumbnailError(
            f"Cannot read a valid Unicode cmap from font {Path(font).name!r}"
        ) from None


def read_cmap(font: Path | str) -> dict[int, int]:
    """Read nonzero glyph mappings using Unicode cmap format 12, or format 4."""
    return _font(font)[1]


def missing_glyphs(font: Path | str, text: str) -> tuple[str, ...]:
    """Return unsupported characters in first-occurrence order; ignore layout controls."""
    mappings = read_cmap(font)
    return _missing(mappings, text)


def _missing(mappings: dict[int, int], text: str) -> tuple[str, ...]:
    return tuple(
        char for char in dict.fromkeys(text) if char not in "\n\r\t" and ord(char) not in mappings
    )


def wrap_title(text: str, *, width: int = 32, max_lines: int = 3) -> str:
    """Wrap Unicode characters, split long words, and ellipsise at the line bound."""
    if type(width) is not int or width < 1 or type(max_lines) is not int or max_lines < 1:
        raise ValueError("width and max_lines must be positive integers")
    lines = textwrap.wrap(
        " ".join(text.split()),
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    )
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: width - 1].rstrip() + "…"
    return "\n".join(lines)


def escape_filter_value(value: Path | str) -> str:
    """Escape an option value for both FFmpeg parser levels, without a shell layer."""
    raw = value.as_posix() if isinstance(value, Path) else str(value)
    option = "".join("\\" + char if char in "\\':" else char for char in raw)
    return "".join("\\" + char if char in "\\'[],;" else char for char in option)


def choose_start(duration: float, skip_seconds: float, *, fps: float = 25) -> float:
    """Shorter-than-skip videos start midway; clips up to 2 s use their middle frame."""
    if (
        not math.isfinite(duration)
        or duration <= 0
        or not math.isfinite(skip_seconds)
        or skip_seconds < 0
    ):
        raise ValueError("duration must be positive and skip_seconds nonnegative")
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be positive")
    start = duration / 2 if duration <= 2 or skip_seconds >= duration else skip_seconds
    return min(start, max(0.0, duration - 1 / fps))


def _advances(
    tables: dict[bytes, bytes], mappings: dict[int, int], size: float
) -> dict[str, float]:
    """Return horizontal glyph advances in pixels at the requested font size."""
    if not math.isfinite(size) or size <= 0:
        raise ValueError("font_size must be positive")
    try:
        units = struct.unpack_from(">H", tables[b"head"], 18)[0]
        count = struct.unpack_from(">H", tables[b"hhea"], 34)[0]
        if not units or not count or len(tables[b"hmtx"]) < 4 * count:
            raise ValueError("invalid font metrics")
        return {
            chr(codepoint): struct.unpack_from(">H", tables[b"hmtx"], 4 * min(glyph, count - 1))[0]
            * size
            / units
            for codepoint, glyph in mappings.items()
        }
    except KeyError, ValueError, struct.error:
        raise ThumbnailError("Font has invalid horizontal metrics") from None


def measure_text(text: str, *, font: Path | str = DEFAULT_FONT, font_size: float = 48) -> float:
    """Measure the widest line as the sum of glyph advances in pixels."""
    tables, mappings = _font(font)
    unsupported = _missing(mappings, text)
    if unsupported:
        raise MissingGlyphs(unsupported)
    widths = _advances(tables, mappings, font_size)
    return max((sum(widths[c] for c in line) for line in text.splitlines()), default=0.0)


def _wrap_measured(text: str, widths: dict[str, float], width: float, max_lines: int) -> str:
    if not math.isfinite(width) or width <= WIDTH_SAFETY:
        raise ValueError("width must exceed the safety margin")
    if type(max_lines) is not int or max_lines < 1:
        raise ValueError("max_lines must be a positive integer")
    limit = width - WIDTH_SAFETY

    def measure(value: str) -> float:
        return sum(widths[char] for char in value)

    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}" if line else word
        if measure(candidate) <= limit:
            line = candidate
            continue
        if line:
            lines.append(line)
            line = ""
        for char in word:
            if widths[char] > limit:
                raise ThumbnailError("A title glyph is wider than the text area")
            if measure(line + char) > limit:
                lines.append(line)
                line = ""
            line += char
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1].rstrip()
        ellipsis = "…"
        if ellipsis not in widths:
            raise MissingGlyphs((ellipsis,))
        if widths[ellipsis] > limit:
            raise ThumbnailError("The text area cannot fit an ellipsis")
        while measure(last + ellipsis) > limit:
            last = last[:-1].rstrip()
        lines[-1] = last + ellipsis
    return "\n".join(lines)


def wrap_measured(
    text: str,
    *,
    font: Path | str = DEFAULT_FONT,
    font_size: float = 48,
    width: float = TEXT_WIDTH,
    max_lines: int = 3,
) -> str:
    """Wrap by summed advances, leaving a small safety margin for text shaping."""
    tables, mappings = _font(font)
    unsupported = _missing(mappings, text)
    if unsupported:
        raise MissingGlyphs(unsupported)
    return _wrap_measured(text, _advances(tables, mappings, font_size), width, max_lines)


def _drawtext_font(font: Path) -> Path:
    """Avoid FreeType's narrow Windows file API silently falling back to another font."""
    if os.name != "nt" or str(font).isascii():
        return font
    short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    short_path.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    short_path.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    count = short_path(str(font), buffer, len(buffer))
    if not 0 < count < len(buffer) or not buffer.value.isascii():
        raise ThumbnailError(
            "Windows drawtext needs an ASCII font path or an ASCII short-name alias"
        )
    return Path(buffer.value)


def _overlay(
    font: Path,
    subtitle_font: Path,
    title_file: Path,
    subtitle_file: Path,
    lines: int,
    has_subtitle: bool,
) -> str:
    panel = 56 + lines * 60 + (40 if has_subtitle else 0)
    filters = [
        # Square pixels first, so a source with non-square pixels keeps its shape. Done with
        # plain scale + setsar: scale's reset_sar option does not exist in the FFmpeg 6.1 of
        # Ubuntu 24.04, and every thumbnail failed on the Google Cloud server (T-1018).
        "scale=trunc(iw*sar/2)*2:ih",
        "setsar=1",
        "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2",
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
        f"drawbox=x=0:y=ih-{panel}:w=iw:h={panel}:color=black@0.72:t=fill",
        f"drawtext=fontfile={escape_filter_value(_drawtext_font(font))}:"
        f"textfile={escape_filter_value(title_file)}:"
        f"expansion=none:fontcolor=white:fontsize=48:line_spacing=12:x=56:y=h-{panel}+28",
    ]
    if has_subtitle:
        filters.append(
            f"drawtext=fontfile={escape_filter_value(_drawtext_font(subtitle_font))}:"
            f"textfile={escape_filter_value(subtitle_file)}:expansion=none:"
            "fontcolor=white:fontsize=28:x=56:y=h-48"
        )
    return ",".join(filters)


def make_thumbnail(
    ffmpeg: Path | str,
    ffprobe: Path | str,
    video: Path | str,
    title: str,
    target: Path | str,
    *,
    subtitle: str = "",
    font: Path | str = DEFAULT_FONT,
    skip_seconds: float = 180,
    on_cancel: CancelHook | None = None,
) -> ThumbnailResult:
    """Select a representative source frame, then atomically write a 1280x720 JPEG.

    A small 10-second analysis window selects sampled frames while preserving their
    timestamps. The final frame is decoded again from the source at the selected time.
    A clip up to 2 seconds samples only its middle frame. No title text enters the graph.
    """
    cancelled, lock = threading.Event(), threading.Lock()
    active: list[Callable[[], None] | None] = [None]

    def cancel() -> None:
        cancelled.set()
        with lock:
            kill = active[0]
        if kill is not None:
            kill()

    def check() -> None:
        if cancelled.is_set():
            raise FfmpegCancelled("Thumbnail creation was stopped")

    def register(kill: Callable[[], None]) -> None:
        with lock:
            active[0] = kill
        if cancelled.is_set():
            kill()

    def run(args: list[Any]) -> None:
        check()
        try:
            run_ffmpeg(ffmpeg, args, on_cancel=register)
        finally:
            with lock:
                active[0] = None
        check()

    if on_cancel is not None:
        on_cancel(cancel)
    check()
    font, target, video = Path(font).resolve(), Path(target), Path(video)
    if target.resolve() == video.resolve():
        raise ThumbnailError("Thumbnail target must differ from the source video")
    tables, mappings = _font(font)
    subtitle_font = REGULAR_FONT if font == DEFAULT_FONT.resolve() else font
    subtitle_tables, subtitle_mappings = _font(subtitle_font) if subtitle else (tables, mappings)
    unsupported = tuple(
        dict.fromkeys((*_missing(mappings, title), *_missing(subtitle_mappings, subtitle)))
    )
    if unsupported:
        raise MissingGlyphs(unsupported)
    wrapped = _wrap_measured(title, _advances(tables, mappings, 48), TEXT_WIDTH, 3)
    if not wrapped:
        raise ThumbnailError("Thumbnail title must not be empty")
    subtext = _wrap_measured(
        subtitle, _advances(subtitle_tables, subtitle_mappings, 28), TEXT_WIDTH, 1
    )
    extra = (*_missing(mappings, wrapped), *_missing(subtitle_mappings, subtext))
    if extra:
        raise MissingGlyphs(tuple(dict.fromkeys(extra)))
    check()
    info = probe(ffprobe, video)
    if not info.video_codec or info.duration_seconds is None:
        raise ThumbnailError("Thumbnail needs a video stream with a known duration")
    fps = info.fps if info.fps is not None and math.isfinite(info.fps) and info.fps > 0 else 25.0
    start = choose_start(info.duration_seconds, skip_seconds, fps=fps)
    window = min(info.duration_seconds - start, 1 / fps if info.duration_seconds <= 2 else 10.0)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".thumbnail-", dir=target.parent) as temporary:
        work = Path(temporary).resolve()
        timing, title_file, subtitle_file = (
            work / "selected.txt",
            work / "title.txt",
            work / "subtitle.txt",
        )
        title_file.write_text(wrapped, encoding="utf-8", newline="\n")
        subtitle_file.write_text(subtext, encoding="utf-8", newline="\n")
        step = max(1, round(fps / 2))
        analysis = (
            f"select='not(mod(n,{step}))',scale=160:-2,thumbnail=n=20,"
            "metadata=mode=add:key=reel_thumbnail:value=1,"
            f"metadata=mode=print:file={escape_filter_value(timing)}"
        )
        run(
            [
                "-v",
                "error",
                "-ss",
                f"{start:.9f}",
                "-t",
                f"{window:.9f}",
                "-i",
                video,
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                analysis,
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ]
        )
        match = _PTS.search(timing.read_text(encoding="utf-8")) if timing.is_file() else None
        if match is None:
            raise ThumbnailError("FFmpeg found no representative frame")
        frame_time = start + float(match.group(1))
        if not math.isfinite(frame_time) or not start <= frame_time < info.duration_seconds:
            raise ThumbnailError("FFmpeg returned an invalid representative frame timestamp")
        graph = _overlay(
            font, subtitle_font, title_file, subtitle_file, len(wrapped.splitlines()), bool(subtext)
        )
        partial = work / "thumbnail.jpg.part"
        for quality in _JPEG_QUALITIES:
            run(
                [
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    f"{frame_time:.9f}",
                    "-i",
                    video,
                    "-map",
                    "0:v:0",
                    "-an",
                    "-sn",
                    "-dn",
                    "-vf",
                    graph,
                    "-frames:v",
                    "1",
                    "-c:v",
                    "mjpeg",
                    "-q:v",
                    str(quality),
                    "-pix_fmt",
                    "yuvj420p",
                    "-f",
                    "image2",
                    "-update",
                    "1",
                    partial,
                ]
            )
            if not partial.is_file() or partial.stat().st_size < 4:
                raise ThumbnailError("FFmpeg produced no JPEG thumbnail")
            size = partial.stat().st_size
            if size < MAX_JPEG_BYTES:
                check()
                os.replace(partial, target)
                log.debug(
                    "Thumbnail created: frame %.6f s, %d bytes, JPEG q=%d",
                    frame_time,
                    size,
                    quality,
                )
                return ThumbnailResult(target, size, frame_time, quality)
        raise ThumbnailError(
            f"Cannot fit thumbnail under {MAX_JPEG_BYTES} bytes at any JPEG quality"
        )
