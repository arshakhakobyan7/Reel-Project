"""The shared Reel card rendered by FFmpeg (T-1018)."""

import subprocess
from pathlib import Path

import pytest

from reel.media.card import LOGO_COLOUR, MARGIN, make_card_thumbnail
from reel.media.ffmpeg import NO_WINDOW, FfmpegCancelled, probe

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "ffmpeg" / "bin"
FFMPEG, FFPROBE = BIN / "ffmpeg.exe", BIN / "ffprobe.exe"
pytestmark = pytest.mark.skipif(
    not (FFMPEG.is_file() and FFPROBE.is_file()), reason="run scripts/setup_ffmpeg.py first"
)


def patch(path, x, y, w=16, h=16):
    """The RGB pixels of a w x h patch of an image."""
    command = [
        str(FFMPEG), "-v", "error", "-i", str(path), "-vf", f"crop={w}:{h}:{x}:{y}",
        "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
    ]  # fmt: skip
    result = subprocess.run(command, capture_output=True, creationflags=NO_WINDOW, timeout=10)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    data = result.stdout
    return [tuple(data[i : i + 3]) for i in range(0, len(data), 3)]


def bright(pixels):
    return sum(1 for p in pixels if min(p) > 200)


def test_card_is_a_1280x720_jpeg_in_the_logo_colour_with_white_text(tmp_path):
    target = tmp_path / "out" / "thumbnail.jpg"
    result = make_card_thumbnail(FFMPEG, target, group="Python 12", lesson="Lecture 14")
    assert result.path == target and target.is_file()
    info = probe(FFPROBE, target)
    assert (info.width, info.height) == (1280, 720)
    assert 4 < result.size_bytes == target.stat().st_size < 2_000_000
    corner = patch(target, 0, 0, 8, 8)
    average = [sum(p[i] for p in corner) / len(corner) for i in range(3)]
    assert all(abs(a - b) <= 12 for a, b in zip(average, LOGO_COLOUR, strict=True))
    assert bright(patch(target, MARGIN, 64, 110, 40)) > 100  # "Reel"
    assert bright(patch(target, MARGIN, 280, 600, 80)) > 1000  # the group name
    assert bright(patch(target, 700, 560, 200, 60)) == 0  # empty background
    assert list(target.parent.iterdir()) == [target]  # no work files left behind


def test_armenian_group_name_is_drawn_with_the_fallback_font(tmp_path):
    target = tmp_path / "thumbnail.jpg"
    make_card_thumbnail(FFMPEG, target, group="Պիթոն 12", lesson="Դաս 3")
    assert bright(patch(target, MARGIN, 280, 500, 80)) > 1000


def test_an_existing_thumbnail_is_replaced(tmp_path):
    target = tmp_path / "thumbnail.jpg"
    target.write_bytes(b"old")
    make_card_thumbnail(FFMPEG, target, group="test", lesson="Lecture 1")
    assert target.read_bytes()[:2] == b"\xff\xd8"  # a JPEG


def test_a_cancelled_call_writes_nothing(tmp_path):
    target = tmp_path / "thumbnail.jpg"

    def cancel_at_once(kill):
        kill()

    with pytest.raises(FfmpegCancelled):
        make_card_thumbnail(
            FFMPEG, target, group="test", lesson="Lecture 1", on_cancel=cancel_at_once
        )
    assert not target.exists()
