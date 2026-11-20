"""Real FFmpeg detection on 29 seconds of synthetic 320x240 media (T-401)."""

import time
from pathlib import Path

import pytest

from reel.cleaning import silence
from reel.cleaning.silence import analyze_silence
from reel.config.schema import SilenceSettings
from reel.media.ffmpeg import FfmpegCancelled, FfmpegError, probe, run_ffmpeg
from reel.media.testmedia import Part, make_lesson

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "ffmpeg" / "bin"
FFMPEG, FFPROBE = BIN / "ffmpeg.exe", BIN / "ffprobe.exe"
pytestmark = pytest.mark.skipif(
    not (FFMPEG.is_file() and FFPROBE.is_file()), reason="run scripts/setup_ffmpeg.py first"
)


def settings(**values) -> SilenceSettings:
    return SilenceSettings(min_silence_seconds=2, keep_seconds=0.25, **values)


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp("silence")
    lesson = make_lesson(
        FFMPEG,
        folder / "lesson.mp4",
        (
            Part(2, "moving", "tone"),
            Part(5, "static", "silence"),
            Part(2, "moving", "tone"),
            Part(4, "moving", "silence"),
            Part(2, "moving", "tone"),
            Part(1, "static", "silence"),
            Part(2, "moving", "tone"),
            Part(4, "static", "silence"),
        ),
    )
    no_audio = make_lesson(
        FFMPEG, folder / "no-audio.mp4", (Part(3, "static", "silence"),), audio=False
    )
    audio_only = folder / "audio.wav"
    run_ffmpeg(
        FFMPEG,
        [
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-t",
            "4",
            "-c:a",
            "pcm_s16le",
            audio_only,
        ],
    )
    return {"lesson": lesson, "no_audio": no_audio, "audio_only": audio_only}


@pytest.fixture(scope="module")
def detected(media):
    progress = []
    result = analyze_silence(
        FFMPEG,
        FFPROBE,
        media["lesson"],
        settings(),
        on_progress=lambda seconds, fraction: progress.append((seconds, fraction)),
    )
    return result, progress


def covers(intervals, seconds):
    return any(item.start <= seconds <= item.end for item in intervals)


def test_long_static_silence_is_removable(detected):
    result, _ = detected
    assert len(result.removals) == 2
    assert covers(result.silences, 4) and covers(result.freezes, 4)
    assert covers(result.removals, 4)
    assert all(item.reason == "silence over a static picture" for item in result.removals)


def test_long_silence_over_changing_picture_is_kept(detected):
    result, _ = detected
    assert covers(result.silences, 11)
    assert not covers(result.freezes, 11)
    assert not covers(result.removals, 11)


def test_short_static_silence_is_kept(detected):
    result, _ = detected
    assert not covers(result.silences, 15.5)
    assert not covers(result.removals, 15.5)


def test_keep_seconds_are_left_at_both_sides(detected):
    result, _ = detected
    raw_start = max(result.silences[0].start, result.freezes[0].start)
    raw_end = min(result.silences[0].end, result.freezes[0].end)
    assert result.removals[0].start == pytest.approx(raw_start + 0.25)
    assert result.removals[0].end == pytest.approx(raw_end - 0.25)
    assert result.removals[0].start == pytest.approx(2.25, abs=0.25)
    assert result.removals[0].end == pytest.approx(6.75, abs=0.25)


def test_silence_and_freeze_still_open_at_eof_are_found(detected):
    result, _ = detected
    assert result.silences[-1].start == pytest.approx(18, abs=0.1)
    assert result.silences[-1].end == pytest.approx(22, abs=0.1)
    assert result.freezes[-1].end == pytest.approx(22, abs=0.1)
    assert result.removals[-1].end == pytest.approx(21.75, abs=0.1)


def test_progress_reports_seconds_and_monotonic_fractions(detected):
    _, progress = detected
    assert progress and progress[-1][1] == pytest.approx(1, abs=0.02)
    assert progress == sorted(progress)
    assert all(0 <= fraction <= 1 for _, fraction in progress)


def test_larger_minimum_changes_the_same_files_result(media):
    cfg = SilenceSettings(min_silence_seconds=6, keep_seconds=0.25)
    result = analyze_silence(FFMPEG, FFPROBE, media["lesson"], cfg)
    assert result.silences == result.removals == []


def test_static_requirement_can_be_disabled(media):
    result = analyze_silence(FFMPEG, FFPROBE, media["lesson"], settings(require_static_video=False))
    assert len(result.removals) == 3 and covers(result.removals, 11)
    assert all(item.reason == "silence" for item in result.removals)


def test_noise_threshold_changes_detection_on_the_same_file(media):
    result = analyze_silence(
        FFMPEG, FFPROBE, media["lesson"], settings(noise_db=-10, require_static_video=False)
    )
    assert len(result.silences) == len(result.removals) == 1
    assert covers(result.removals, 1)  # Even the synthetic tone is below -10 dB.


def test_freeze_threshold_changes_detection_on_the_same_file(media):
    result = analyze_silence(FFMPEG, FFPROBE, media["lesson"], settings(freeze_noise=1))
    assert covers(result.freezes, 11) and covers(result.removals, 11)


def test_no_audio_gives_no_silences_but_still_reports_freezes(media):
    result = analyze_silence(FFMPEG, FFPROBE, media["no_audio"], settings())
    assert result.silences == result.removals == []
    assert covers(result.freezes, 1)


def test_audio_only_needs_static_gate_disabled(media):
    guarded = analyze_silence(FFMPEG, FFPROBE, media["audio_only"], settings())
    unguarded = analyze_silence(
        FFMPEG, FFPROBE, media["audio_only"], settings(require_static_video=False)
    )
    assert guarded.silences and guarded.freezes == guarded.removals == []
    assert len(unguarded.removals) == 1
    assert unguarded.removals[0].start == pytest.approx(0.25)
    assert unguarded.removals[0].end == pytest.approx(3.75)


def test_cancellation_stops_ffmpeg(media):
    started = time.monotonic()
    with pytest.raises(FfmpegCancelled):
        analyze_silence(FFMPEG, FFPROBE, media["lesson"], settings(), on_cancel=lambda kill: kill())
    assert time.monotonic() - started < 5


def test_progress_callback_can_cancel_running_analysis(media):
    hooks = []
    with pytest.raises(FfmpegCancelled):
        analyze_silence(
            FFMPEG,
            FFPROBE,
            media["lesson"],
            settings(),
            on_cancel=hooks.append,
            on_progress=lambda *_: hooks[0](),
        )


def test_progress_callback_failure_closes_process_and_pipes(media):
    def fail(*_):
        raise RuntimeError("progress failed")

    with pytest.raises(RuntimeError, match="progress failed"):
        analyze_silence(FFMPEG, FFPROBE, media["lesson"], settings(), on_progress=fail)


def test_missing_ffmpeg_raises_runner_error(media, tmp_path):
    with pytest.raises(FfmpegError, match="cannot run"):
        analyze_silence(tmp_path / "missing-ffmpeg.exe", FFPROBE, media["lesson"], settings())


def test_ffmpeg_failure_keeps_diagnostic_message(media, tmp_path, monkeypatch):
    info = probe(FFPROBE, media["lesson"])
    monkeypatch.setattr(silence, "probe", lambda *args: info)
    with pytest.raises(FfmpegError, match="exit code") as error:
        analyze_silence(FFMPEG, FFPROBE, tmp_path / "missing.mp4", settings())
    assert "missing.mp4" in str(error.value)
