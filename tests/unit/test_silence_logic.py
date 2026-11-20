"""Pure detection logic and command construction, without FFmpeg (T-401)."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from reel.cleaning import silence
from reel.cleaning.silence import (
    Interval,
    Removal,
    analyze_silence,
    intersect_intervals,
    parse_detection_log,
    removable_silences,
)
from reel.config.schema import SilenceSettings
from reel.media.ffmpeg import InvalidMedia, MediaInfo


def settings(**values) -> SilenceSettings:
    return SilenceSettings(min_silence_seconds=2, keep_seconds=0.25, **values)


def test_parse_interleaved_events_and_ignore_duration_messages():
    output = """
    [silencedetect @ abc] silence_start: 1.25
    [freezedetect @ def] lavfi.freezedetect.freeze_start: 2
    unrelated diagnostics
    [silencedetect @ abc] silence_end: 6.5 | silence_duration: 5.25
    [freezedetect @ def] lavfi.freezedetect.freeze_duration: 5
    [freezedetect @ def] lavfi.freezedetect.freeze_end: 7
    """
    assert parse_detection_log(output, 10) == ([Interval(1.25, 6.5)], [Interval(2, 7)])


def test_open_silence_and_freeze_end_at_media_duration():
    assert parse_detection_log(["silence_start: 3", "freeze_start: 4"], 12) == (
        [Interval(3, 12)],
        [Interval(4, 12)],
    )


def test_parser_clips_times_and_handles_duplicates_and_unmatched_events():
    output = """
    silence_end: 0.5
    silence_start: -2e-1
    silence_start: 1
    silence_end: 3
    silence_start: 3
    silence_end: 50
    freeze_start: 7
    freeze_end: 7
    freeze_start: 20
    """
    assert parse_detection_log(output, 10) == ([Interval(0, 10)], [])


def test_empty_and_nonfinite_events_do_not_create_intervals():
    assert parse_detection_log("silence_start: nan\nfreeze_start: 1e999", 10) == ([], [])
    assert parse_detection_log("silence_start: 0\nfreeze_start: 0", 0) == ([], [])
    assert parse_detection_log("", 10) == ([], [])


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan")])
def test_parser_rejects_invalid_duration(duration):
    with pytest.raises(ValueError, match="duration"):
        parse_detection_log("", duration)


@pytest.mark.parametrize("start,end", [(-1, 1), (2, 2), (3, 2), (0, float("inf"))])
def test_interval_rejects_invalid_bounds(start, end):
    with pytest.raises(ValueError, match="interval"):
        Interval(start, end)


def test_removal_is_frozen():
    item = Removal(1, 3, "silence")
    with pytest.raises(FrozenInstanceError):
        item.start = 2


def test_intersection_sorts_and_merges_without_changing_inputs():
    left = [Interval(10, 20), Interval(0, 5), Interval(4, 8)]
    right = [Interval(15, 25), Interval(3, 6), Interval(6, 12)]
    original = left.copy(), right.copy()
    assert intersect_intervals(left, right) == [Interval(3, 8), Interval(10, 12), Interval(15, 20)]
    assert (left, right) == original
    assert intersect_intervals([Interval(0, 2)], [Interval(2, 4)]) == []
    assert intersect_intervals([], right) == []


def test_long_static_silence_is_shrunk_on_both_sides():
    assert removable_silences([Interval(0, 10)], [Interval(2, 8)], settings()) == [
        Removal(2.25, 7.75, "silence over a static picture")
    ]


def test_quiet_changing_picture_is_kept():
    assert removable_silences([Interval(0, 10)], [], settings()) == []


def test_short_silence_and_short_overlap_are_kept():
    assert removable_silences([Interval(0, 1)], [Interval(0, 10)], settings()) == []
    assert removable_silences([Interval(0, 10)], [Interval(8.5, 12)], settings()) == []


def test_minimum_is_applied_before_padding_and_removals_are_ordered():
    assert removable_silences(
        [Interval(10, 12), Interval(0, 2)], [Interval(0, 20)], settings()
    ) == [
        Removal(0.25, 1.75, "silence over a static picture"),
        Removal(10.25, 11.75, "silence over a static picture"),
    ]


def test_static_gate_can_be_disabled_and_zero_padding_is_respected():
    cfg = SilenceSettings(min_silence_seconds=2, keep_seconds=0, require_static_video=False)
    assert removable_silences([Interval(0, 5)], [], cfg) == [Removal(0, 5, "silence")]


@pytest.fixture
def media_info() -> MediaInfo:
    return MediaInfo(
        path="lesson.mp4",
        size_bytes=1,
        container="mp4",
        duration_seconds=20,
        video_codec="h264",
        width=320,
        height=240,
        fps=25,
        audio_codec="aac",
        sample_rate=48000,
        channels=1,
        creation_time=None,
    )


def test_one_pass_uses_all_config_thresholds_and_forwards_callbacks(monkeypatch, media_info):
    calls = []
    progress, cancel = object(), object()
    monkeypatch.setattr(silence, "probe", lambda *args: media_info)

    def run(binary, args, **kwargs):
        calls.append((binary, args, kwargs))
        return ["silence_start: 2", "freeze_start: 3", "silence_end: 12", "freeze_end: 14"]

    monkeypatch.setattr(silence, "_run_analysis", run)
    cfg = SilenceSettings(
        noise_db=-47.5, min_silence_seconds=3.5, keep_seconds=0.5, freeze_noise=0.008
    )
    result = analyze_silence(
        "ffmpeg", "ffprobe", Path("lesson.mp4"), cfg, on_progress=progress, on_cancel=cancel
    )
    assert len(calls) == 1
    binary, args, kwargs = calls[0]
    assert binary == "ffmpeg" and args.count("-i") == 1
    assert args[args.index("-af") + 1] == "silencedetect=noise=-47.5dB:d=3.5"
    assert args[args.index("-vf") + 1] == "fps=5,scale=160:-2,freezedetect=n=0.008:d=3.5"
    assert args[-3:] == ["-f", "null", "-"]
    assert kwargs == {"duration": 20, "on_progress": progress, "on_cancel": cancel}
    assert result.silences == [Interval(2, 12)]
    assert result.freezes == [Interval(3, 14)]
    assert result.removals == [Removal(3.5, 11.5, "silence over a static picture")]


@pytest.mark.parametrize("audio,video", [(False, True), (True, False), (False, False)])
def test_absent_streams_are_not_mapped(monkeypatch, media_info, audio, video):
    info = replace(
        media_info, audio_codec="aac" if audio else None, video_codec="h264" if video else None
    )
    monkeypatch.setattr(silence, "probe", lambda *args: info)
    calls = []

    def run(binary, args, **kwargs):
        calls.append(args)
        return []

    monkeypatch.setattr(silence, "_run_analysis", run)
    result = analyze_silence("ffmpeg", "ffprobe", "lesson.mp4", settings())
    assert result.silences == result.freezes == result.removals == []
    if audio or video:
        assert ("0:a:0" in calls[0]) == audio
        assert ("0:v:0" in calls[0]) == video
    else:
        assert calls == []


@pytest.mark.parametrize("duration", [None, 0, -1, float("nan"), float("inf")])
def test_unknown_or_invalid_media_duration_is_rejected(monkeypatch, media_info, duration):
    monkeypatch.setattr(
        silence, "probe", lambda *args: replace(media_info, duration_seconds=duration)
    )
    with pytest.raises(InvalidMedia, match="known positive media duration"):
        analyze_silence("ffmpeg", "ffprobe", "lesson.mp4", settings())


@pytest.mark.parametrize("duration", [None, 0, -1, float("nan"), float("inf")])
def test_no_audio_with_unknown_duration_returns_no_silences(monkeypatch, media_info, duration):
    monkeypatch.setattr(
        silence,
        "probe",
        lambda *args: replace(media_info, audio_codec=None, duration_seconds=duration),
    )

    def unexpected_run(*args, **kwargs):
        pytest.fail("unknown-duration video without audio does not need an analysis pass")

    monkeypatch.setattr(silence, "_run_analysis", unexpected_run)
    result = analyze_silence("ffmpeg", "ffprobe", "lesson.mp4", settings())
    assert result.silences == result.freezes == result.removals == []
