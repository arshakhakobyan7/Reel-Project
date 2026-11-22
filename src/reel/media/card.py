"""The shared Reel thumbnail: one card for every video (T-1018, the user's choice 2026-11-22).

A diagonal gradient from the Picsart logo colour (#C209C1) to a deep violet, with soft round
shapes on the right; on it the group name, the lesson ("Lecture 14") and "Reel", in the Outfit
font (OFL), close to Picsart's own Gilroy. Text Outfit cannot draw (Armenian, for example) is
drawn with DejaVu Sans Bold instead.

The background is made with numpy and given to FFmpeg as raw pixels, so nothing depends on
the FFmpeg version (the server runs Ubuntu's 6.1). Text goes through ``textfile`` with
``expansion=none``, so it is always drawn literally.
"""

import functools
import logging
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from reel.media.ffmpeg import CancelHook, FfmpegCancelled, run_ffmpeg
from reel.media.thumbnail import (
    _JPEG_QUALITIES,
    DEFAULT_FONT,
    FONT_DIR,
    MAX_JPEG_BYTES,
    WIDTH_SAFETY,
    MissingGlyphs,
    ThumbnailError,
    ThumbnailResult,
    _advances,
    _drawtext_font,
    _font,
    _missing,
    _wrap_measured,
    escape_filter_value,
)

log = logging.getLogger("reel.media")

WIDTH, HEIGHT = 1280, 720
MARGIN = 80
TEXT_WIDTH = WIDTH - 2 * MARGIN
CARD_FONT = FONT_DIR / "Outfit-Bold.ttf"
CARD_FONT_SEMIBOLD = FONT_DIR / "Outfit-SemiBold.ttf"
FALLBACK_FONT = DEFAULT_FONT  # DejaVu Sans Bold: Armenian and Latin
LOGO_COLOUR = (0xC2, 0x09, 0xC1)  # the Picsart logo (its SVG uses this one colour)
DEEP_VIOLET = (0x4B, 0x0A, 0xA8)
GROUP_SIZES = (124, 112, 100, 88)  # the largest size that fits one line is used
LESSON_SIZE = 70
BRAND_SIZE = 46
BRAND = "Reel"


@functools.cache
def background() -> bytes:
    """The card's 1280x720 background as raw RGB24 pixels (the same for every video)."""
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float64)
    share = np.clip(x / WIDTH * 0.6 + y / HEIGHT * 0.4, 0, 1)[..., None]
    image = np.array(LOGO_COLOUR, float) * (1 - share) + np.array(DEEP_VIOLET, float) * share
    white = np.array([255.0, 255.0, 255.0])

    def add(alpha: np.ndarray) -> None:
        nonlocal image
        image = image * (1 - alpha[..., None]) + white * alpha[..., None]

    distance = np.hypot(x - 1090, y - 380)
    add(0.10 * np.clip(1 - np.abs(distance - 250) / 10, 0, 1))  # a thin ring
    add(0.07 * np.clip(150 - np.hypot(x - 1190, y - 110) + 0.5, 0, 1))  # two soft discs
    add(0.05 * np.clip(110 - np.hypot(x - 960, y - 640) + 0.5, 0, 1))
    return np.rint(np.clip(image, 0, 255)).astype(np.uint8).tobytes()


def font_for(text: str, preferred: Path) -> Path:
    """``preferred`` when it has every character of ``text``, else DejaVu Sans Bold."""
    if not _missing(_font(preferred)[1], text):
        return preferred
    missing = _missing(_font(FALLBACK_FONT)[1], text)
    if missing:
        raise MissingGlyphs(missing)
    return FALLBACK_FONT


def fit_group(group: str) -> tuple[Path, str, int]:
    """The font, the (wrapped) text and the size of the group name: one line at the largest
    size that fits, else up to two lines at the smallest size, shortened with an ellipsis."""
    text = " ".join(group.split())
    if not text:
        raise ThumbnailError("The thumbnail needs a group name")
    font = font_for(text, CARD_FONT)
    tables, mappings = _font(font)
    for size in GROUP_SIZES:
        widths = _advances(tables, mappings, size)
        if sum(widths[char] for char in text) <= TEXT_WIDTH - WIDTH_SAFETY:
            return font, text, size
    size = GROUP_SIZES[-1]
    if "…" not in mappings:
        font = FALLBACK_FONT
        tables, mappings = _font(font)
    return font, _wrap_measured(text, _advances(tables, mappings, size), TEXT_WIDTH, 2), size


def _text(font: Path, textfile: Path, size: int, x: int, y: int, colour: str) -> str:
    spacing = round(size * 0.12)
    return (
        f"drawtext=fontfile={escape_filter_value(_drawtext_font(font))}:"
        f"textfile={escape_filter_value(textfile)}:expansion=none:fontcolor={colour}:"
        f"fontsize={size}:line_spacing={spacing}:x={x}:y={y}"
    )


def card_graph(work: Path, group: str, lesson: str) -> str:
    """Write the three texts into ``work`` and return the drawtext graph that places them."""
    group_font, group_text, group_size = fit_group(group)
    lesson = " ".join(lesson.split())
    lesson_font = font_for(lesson, CARD_FONT_SEMIBOLD) if lesson else CARD_FONT_SEMIBOLD
    lines = group_text.count("\n") + 1
    group_height = lines * group_size + (lines - 1) * round(group_size * 0.12)
    block = group_height + (26 + LESSON_SIZE if lesson else 0)
    top = max(150, (HEIGHT - block) // 2 + 10)
    files = {"brand": BRAND, "group": group_text, "lesson": lesson}
    for name, value in files.items():
        (work / f"{name}.txt").write_text(value, encoding="utf-8", newline="\n")
    parts = [
        _text(CARD_FONT, work / "brand.txt", BRAND_SIZE, MARGIN, 64, "white"),
        _text(group_font, work / "group.txt", group_size, MARGIN, top, "white"),
    ]
    if lesson:
        lesson_top = top + group_height + 26
        parts.append(
            _text(
                lesson_font, work / "lesson.txt", LESSON_SIZE, MARGIN + 4, lesson_top, "white@0.88"
            )
        )
    return ",".join(parts)


def make_card_thumbnail(
    ffmpeg: Path | str,
    target: Path | str,
    *,
    group: str,
    lesson: str,
    on_cancel: CancelHook | None = None,
) -> ThumbnailResult:
    """Atomically write the 1280x720 JPEG card for ``group`` and ``lesson`` to ``target``."""
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
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".thumbnail-", dir=target.parent) as temporary:
        work = Path(temporary).resolve()
        graph = card_graph(work, group, lesson)
        pixels = work / "background.rgb"
        pixels.write_bytes(background())
        partial = work / "thumbnail.jpg.part"
        for quality in _JPEG_QUALITIES:
            run(
                ["-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                 "-s", f"{WIDTH}x{HEIGHT}", "-i", pixels, "-vf", graph, "-frames:v", "1",
                 "-c:v", "mjpeg", "-q:v", str(quality), "-pix_fmt", "yuvj420p",
                 "-f", "image2", "-update", "1", partial]
            )  # fmt: skip
            if not partial.is_file() or partial.stat().st_size < 4:
                raise ThumbnailError("FFmpeg produced no JPEG thumbnail")
            size = partial.stat().st_size
            if size < MAX_JPEG_BYTES:
                check()
                os.replace(partial, target)
                log.debug("Thumbnail card created: %d bytes, JPEG q=%d", size, quality)
                return ThumbnailResult(target, size, 0.0, quality)
    raise ThumbnailError(f"Cannot fit thumbnail under {MAX_JPEG_BYTES} bytes at any JPEG quality")
