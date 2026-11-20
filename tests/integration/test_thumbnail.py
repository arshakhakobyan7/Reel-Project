"""Real thumbnail generation from 9.04 seconds of synthetic video only (T-503)."""

import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from reel.media import thumbnail
from reel.media.ffmpeg import NO_WINDOW, FfmpegCancelled, probe, run_ffmpeg
from reel.media.testmedia import Part, make_lesson
from reel.media.thumbnail import DEFAULT_FONT, MissingGlyphs, ThumbnailError, make_thumbnail

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "ffmpeg" / "bin"
FFMPEG, FFPROBE = BIN / "ffmpeg.exe", BIN / "ffprobe.exe"
pytestmark = pytest.mark.skipif(
    not (FFMPEG.is_file() and FFPROBE.is_file()), reason="run scripts/setup_ffmpeg.py first"
)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    folder = tmp_path_factory.mktemp("thumbnail-media")
    return {
        "lesson": make_lesson(
            FFMPEG,
            folder / "lesson.mp4",
            (
                Part(2, "static", "silence"),
                Part(4, "moving", "tone"),
            ),
        ),
        "short": make_lesson(FFMPEG, folder / "short.mp4", (Part(1),), audio=False),
        "single": make_lesson(FFMPEG, folder / "single.mp4", (Part(0.04, "static"),), audio=False),
        "portrait": make_lesson(
            FFMPEG, folder / "portrait.mp4", (Part(2, "static"),), size="160x240", audio=False
        ),
    }


def test_exact_jpeg_size_format_byte_limit_and_armenian_title(media, tmp_path):
    title = "Հայերեն ծրագրավորման դաս. բառարաններ, ֆունկցիաներ և գործնական օրինակներ"
    target = tmp_path / "thumbnail.jpg"
    result = make_thumbnail(
        FFMPEG,
        FFPROBE,
        media["lesson"],
        title,
        target,
        subtitle="Python 12 · Դաս 14",
        skip_seconds=2,
    )
    info = probe(FFPROBE, result.path)
    assert (info.width, info.height, info.video_codec) == (1280, 720, "mjpeg")
    assert target.read_bytes().startswith(b"\xff\xd8") and target.read_bytes().endswith(b"\xff\xd9")
    assert 0 < result.size_bytes == target.stat().st_size < 2_000_000
    assert result.quality == 2 and 2 <= result.frame_time < 6
    assert list(tmp_path.iterdir()) == [target]


def test_default_skip_is_reduced_for_shorter_video(media, tmp_path):
    result = make_thumbnail(FFMPEG, FFPROBE, media["lesson"], "Դաս", tmp_path / "shorter.jpg")
    assert 3 <= result.frame_time < 6


@pytest.mark.parametrize("name,middle", [("short", 0.5), ("single", 0)])
def test_very_short_video_uses_middle_or_only_frame(media, tmp_path, name, middle):
    result = make_thumbnail(FFMPEG, FFPROBE, media[name], "Դաս", tmp_path / "thumb.jpg")
    assert result.frame_time == pytest.approx(middle, abs=0.04)
    assert probe(FFPROBE, result.path).width == 1280


def test_special_title_subtitle_and_paths_are_literal(media, tmp_path):
    folder = tmp_path / "O'Brien, [Դաս]; 100%"
    folder.mkdir()
    font = folder / "O'Brien, [bold].ttf"
    shutil.copyfile(DEFAULT_FONT, font)
    target = folder / "thumb's, [one].jpg"
    result = make_thumbnail(
        FFMPEG,
        FFPROBE,
        media["lesson"],
        "a:b'c\\d, 50% %{x};[]",
        target,
        subtitle="Subtitle: 'quote' \\ 100% %{invalid}",
        font=font,
        skip_seconds=2,
    )
    assert result.path == target and probe(FFPROBE, target).video_codec == "mjpeg"
    plain_font = tmp_path / "bold.ttf"
    shutil.copyfile(DEFAULT_FONT, plain_font)
    baseline = make_thumbnail(
        FFMPEG,
        FFPROBE,
        media["lesson"],
        "a:b'c\\d, 50% %{x};[]",
        tmp_path / "baseline.jpg",
        subtitle="Subtitle: 'quote' \\ 100% %{invalid}",
        font=plain_font,
        skip_seconds=2,
    )
    assert target.read_bytes() == baseline.path.read_bytes()  # No silent font substitution.
    assert set(folder.iterdir()) == {font, target}


def gray_patch(path, x, y):
    command = [
        str(FFMPEG),
        "-v",
        "error",
        "-i",
        str(path),
        "-vf",
        f"crop=16:16:{x}:{y}",
        "-frames:v",
        "1",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, creationflags=NO_WINDOW, timeout=10)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout


def test_portrait_aspect_ratio_is_kept_with_black_padding(media, tmp_path):
    result = make_thumbnail(
        FFMPEG, FFPROBE, media["portrait"], "Portrait", tmp_path / "portrait.jpg"
    )
    assert max(gray_patch(result.path, 0, 20)) <= 8
    middle = gray_patch(result.path, 632, 20)
    assert 50 <= sum(middle) / len(middle) <= 80  # The original static picture is gray 64.


def test_missing_glyph_refuses_instead_of_replacing_existing_target(media, tmp_path):
    target = tmp_path / "existing.jpg"
    target.write_bytes(b"existing target")
    with pytest.raises(MissingGlyphs, match="U\\+6F22"):
        make_thumbnail(FFMPEG, FFPROBE, media["lesson"], "漢", target)
    assert target.read_bytes() == b"existing target" and list(tmp_path.iterdir()) == [target]


def test_quality_is_lowered_until_the_same_image_fits(media, tmp_path, monkeypatch):
    first = make_thumbnail(
        FFMPEG, FFPROBE, media["lesson"], "Quality", tmp_path / "first.jpg", skip_seconds=2
    )
    monkeypatch.setattr(thumbnail, "MAX_JPEG_BYTES", first.size_bytes - 1)
    second = make_thumbnail(
        FFMPEG, FFPROBE, media["lesson"], "Quality", tmp_path / "second.jpg", skip_seconds=2
    )
    assert second.quality > first.quality and second.size_bytes < thumbnail.MAX_JPEG_BYTES
    assert second.frame_time == first.frame_time


def test_impossible_size_limit_fails_clearly_and_preserves_target(media, tmp_path, monkeypatch):
    monkeypatch.setattr(thumbnail, "MAX_JPEG_BYTES", 4)
    monkeypatch.setattr(thumbnail, "_JPEG_QUALITIES", (2, 31))
    target = tmp_path / "existing.jpg"
    target.write_bytes(b"old JPEG")
    with pytest.raises(ThumbnailError, match="Cannot fit"):
        make_thumbnail(FFMPEG, FFPROBE, media["lesson"], "Quality", target, skip_seconds=2)
    assert target.read_bytes() == b"old JPEG" and list(tmp_path.iterdir()) == [target]


def test_cancellation_kills_running_ffmpeg_and_cleans_partial_files(media, tmp_path, monkeypatch):
    started, hooks, errors = threading.Event(), [], []
    real_run = run_ffmpeg

    def realtime(ffmpeg, args, *, on_cancel):
        def register(kill):
            on_cancel(kill)
            started.set()

        return real_run(ffmpeg, ["-re", *args], on_cancel=register)

    monkeypatch.setattr(thumbnail, "run_ffmpeg", realtime)
    target = tmp_path / "existing.jpg"
    target.write_bytes(b"old JPEG")

    def work():
        try:
            make_thumbnail(
                FFMPEG,
                FFPROBE,
                media["lesson"],
                "Դաս",
                target,
                skip_seconds=2,
                on_cancel=hooks.append,
            )
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    assert started.wait(5)
    cancelled_at = time.monotonic()
    hooks[0]()
    thread.join(5)
    assert not thread.is_alive() and time.monotonic() - cancelled_at < 5
    assert len(errors) == 1 and isinstance(errors[0], FfmpegCancelled)
    assert target.read_bytes() == b"old JPEG" and list(tmp_path.iterdir()) == [target]


def test_review_title_is_written_in_full_to_rendered_textfile(media, tmp_path, monkeypatch):
    title = "Սեփական ֆունկցիաներ, callable օբյեկտներ և hash աղյուսակներ Python-ում"
    real_run, captured = thumbnail.run_ffmpeg, []

    def capture(ffmpeg, args, **kwargs):
        graph = str(args[args.index("-vf") + 1])
        if "drawtext=" in graph:
            work = Path(args[-1]).parent
            captured.append((work / "title.txt").read_text(encoding="utf-8"))
        return real_run(ffmpeg, args, **kwargs)

    monkeypatch.setattr(thumbnail, "run_ffmpeg", capture)
    result = make_thumbnail(
        FFMPEG,
        FFPROBE,
        media["lesson"],
        title,
        tmp_path / "review.jpg",
        subtitle="Python 12 · Դաս 14",
        skip_seconds=2,
    )
    assert " ".join(captured[0].splitlines()) == title
    assert len(captured[0].splitlines()) <= 3 and "…" not in captured[0]
    assert probe(FFPROBE, result.path).width == 1280
