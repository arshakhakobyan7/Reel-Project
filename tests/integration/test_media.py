"""Media toolkit on generated test media (T-201): probe, validate, extract audio, runner."""

import re
import subprocess
import sys
import threading
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest

from reel.media.ffmpeg import (
    FfmpegCancelled,
    FfmpegError,
    InvalidMedia,
    extract_audio,
    probe,
    progress_seconds,
    run_ffmpeg,
    validate,
)
from reel.media.testmedia import Part, make_lesson, parse_parts

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "ffmpeg" / "bin"
FFMPEG, FFPROBE = BIN / "ffmpeg.exe", BIN / "ffprobe.exe"
pytestmark = pytest.mark.skipif(not FFMPEG.is_file(), reason="run scripts/setup_ffmpeg.py first")
CREATED = datetime(2026, 11, 18, 10, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp("media")
    lesson = make_lesson(FFMPEG, folder / "lesson.mp4", creation_time=CREATED)  # 3 + 4 + 3 s
    fast = make_lesson(FFMPEG, folder / "fast.mp4", faststart=True)
    return {
        "lesson": lesson,
        "fast": fast,
        "silent": make_lesson(FFMPEG, folder / "silent.mp4", (Part(2),), audio=False),
        "folder": folder,
    }


def cut(source: Path, target: Path, share: float) -> Path:
    target.write_bytes(source.read_bytes()[: int(source.stat().st_size * share)])
    return target


def test_probe_values_match_the_generated_file(media):
    info = probe(FFPROBE, media["lesson"])
    assert info.duration_seconds == pytest.approx(10.0, abs=0.1)
    assert (info.video_codec, info.width, info.height, info.fps) == ("h264", 320, 240, 25.0)
    assert (info.audio_codec, info.sample_rate, info.channels) == ("aac", 48000, 1)
    assert info.creation_time == CREATED
    assert info.size_bytes == media["lesson"].stat().st_size
    assert "mp4" in info.container
    assert info.to_dict()["creation_time"] == "2026-11-18T10:30:00+00:00"


def test_a_good_file_passes_validation(media):
    assert validate(FFMPEG, FFPROBE, media["lesson"], min_duration_seconds=5).width == 320


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("garbage", "not a readable media file"),
        ("truncated", "not a readable media file"),  # index (moov) at the end is missing
        ("truncated_fast", "truncated"),  # index first: the end is missing
        ("silent", "no audio stream"),
        ("short", "too short: 10.0 s (minimum 300.0 s)"),
    ],
)
def test_bad_files_fail_validation_with_a_reason(media, name, reason):
    folder = media["folder"]
    paths = {
        "garbage": folder / "garbage.mp4",
        "truncated": folder / "truncated.mp4",
        "truncated_fast": folder / "truncated_fast.mp4",
        "silent": media["silent"],
        "short": media["lesson"],
    }
    paths["garbage"].write_bytes(b"not a video " * 500)
    cut(media["lesson"], paths["truncated"], 0.6)
    cut(media["fast"], paths["truncated_fast"], 0.6)
    minimum = 300 if name == "short" else 0
    with pytest.raises(InvalidMedia, match=re.escape(reason)):
        validate(FFMPEG, FFPROBE, paths[name], min_duration_seconds=minimum)


def test_extract_audio_gives_16_khz_mono_with_the_same_duration(media, tmp_path):
    progress = []
    target = tmp_path / "job" / "audio.wav"
    done = extract_audio(
        FFMPEG, media["lesson"], target, duration=10.0, on_progress=lambda s, f: progress.append(f)
    )
    assert done == target and not (tmp_path / "job" / "audio.wav.part").exists()
    with wave.open(str(target)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        assert wav.getnframes() / wav.getframerate() == pytest.approx(10.0, abs=0.05)
    assert progress and progress[-1] == pytest.approx(1.0, abs=0.05)
    assert progress == sorted(progress)


def test_a_failed_extraction_leaves_no_file(media, tmp_path):
    target = tmp_path / "audio.wav"
    with pytest.raises(FfmpegError):
        extract_audio(FFMPEG, media["silent"], target)  # no audio track to extract
    assert list(tmp_path.iterdir()) == []


def test_ffmpeg_can_be_stopped(tmp_path):
    hooks = []
    errors = []
    args = ["-re", "-f", "lavfi", "-i", "testsrc2=duration=30", "-f", "null", "-"]

    def work():
        try:
            run_ffmpeg(FFMPEG, args, on_cancel=hooks.append)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=work)
    thread.start()
    while not hooks:
        time.sleep(0.01)
    time.sleep(0.3)
    started = time.monotonic()
    hooks[0]()  # what StageContext.request_cancel does
    thread.join(10)
    assert time.monotonic() - started < 5
    assert len(errors) == 1 and isinstance(errors[0], FfmpegCancelled)


def test_ffmpeg_errors_carry_its_message():
    with pytest.raises(FfmpegError, match="exit code") as info:
        run_ffmpeg(FFMPEG, ["-v", "error", "-i", "missing-file.mp4", "-f", "null", "-"])
    assert "missing-file.mp4" in str(info.value)


def test_progress_lines():
    assert progress_seconds("out_time_us=2500000") == 2.5
    assert progress_seconds("out_time_ms=1000000\n") == 1.0  # FFmpeg reports µs here too
    assert progress_seconds("out_time_us=-50") == 0.0
    assert progress_seconds("out_time_us=N/A") is None
    assert progress_seconds("frame=12") is None


def test_parts_text_and_the_script(tmp_path):
    assert parse_parts("moving:3:noise, static:2:silence") == (
        Part(3, "moving", "noise"),
        Part(2, "static", "silence"),
    )
    target = tmp_path / "s.mp4"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_test_media.py"), str(target)]
        + ["--parts", "moving:1:tone,static:1:silence"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert probe(FFPROBE, target).duration_seconds == pytest.approx(2.0, abs=0.1)
