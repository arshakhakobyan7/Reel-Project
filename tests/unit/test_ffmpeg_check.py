"""Tests for parsing FFmpeg capability listings (T-002)."""

from pathlib import Path

import pytest

from reel.media.ffmpeg_check import (
    REQUIRED_ENCODERS,
    REQUIRED_FILTERS,
    FfmpegError,
    check_capabilities,
    parse_encoders,
    parse_filters,
    run_tool,
)

FILTERS_SAMPLE = """\
Filters:
  T.. = Timeline support
  .S. = Slice threading
  ..C = Command support
  A = Audio input/output
  V = Video input/output
  N = Dynamic number and/or type of input/output
  | = Source or sink filter
 ... abench            A->A       Benchmark part of a filtergraph.
 T.C afade             A->A       Fade in/out input audio.
 ... concat            N->N       Concatenate audio and video streams.
 ... anullsrc          |->A       Null audio source, return empty audio frames.
 TSC overlay           VV->V      Overlay a video source on top of the input.
 ... silencedetect     A->A       Detect silence.
 ... nullsink          V->|       Do absolutely nothing with the input video.
"""

ENCODERS_SAMPLE = """\
Encoders:
 V..... = Video
 A..... = Audio
 S..... = Subtitle
 .F.... = Frame-level multithreading
 ..S... = Slice-level multithreading
 ...X.. = Codec is experimental
 ....B. = Supports draw_horiz_band
 .....D = Supports direct rendering method 1
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC (codec h264)
 A....D aac                  AAC (Advanced Audio Coding)
 S..... mov_text             3GPP Timed Text subtitle
"""


def test_parse_filters_reads_names_and_skips_legend():
    names = parse_filters(FILTERS_SAMPLE)
    assert {"abench", "afade", "concat", "anullsrc", "overlay", "silencedetect"} <= names
    assert "nullsink" in names
    assert not names & {"T..", "=", "A", "V", "N", "|"}


def test_parse_encoders_reads_names_after_separator():
    assert parse_encoders(ENCODERS_SAMPLE) == {"libx264", "aac", "mov_text"}


def _fake_runner(filters: str, encoders: str):
    def runner(binary: Path, *args: str) -> str:
        if args == ("-filters",):
            return filters
        if args == ("-encoders",):
            return encoders
        if args == ("-version",):
            return f"{binary.stem} version 9.9-test Copyright (c)\nconfiguration: ...\n"
        raise AssertionError(f"unexpected call {binary} {args}")

    return runner


def test_check_reports_missing_features():
    report = check_capabilities(
        Path("ffmpeg.exe"),
        Path("ffprobe.exe"),
        runner=_fake_runner(FILTERS_SAMPLE, ENCODERS_SAMPLE.replace("libx264", "libx265")),
    )
    assert not report.ok
    assert "freezedetect" in report.missing_filters
    assert "silencedetect" not in report.missing_filters
    assert report.missing_encoders == ("libx264",)
    assert report.ffmpeg_version == "ffmpeg version 9.9-test Copyright (c)"
    assert report.ffprobe_version == "ffprobe version 9.9-test Copyright (c)"


def test_check_ok_when_everything_is_present():
    filters = "\n".join(f" ... {name:<16} A->A       test" for name in REQUIRED_FILTERS)
    encoders = "------\n" + "\n".join(f" A....D {name:<20} test" for name in REQUIRED_ENCODERS)
    report = check_capabilities(
        Path("ffmpeg.exe"), Path("ffprobe.exe"), runner=_fake_runner(filters, encoders)
    )
    assert report.ok


def test_run_tool_reports_missing_binary(tmp_path):
    with pytest.raises(FfmpegError, match="not found"):
        run_tool(tmp_path / "no-such-ffmpeg.exe", "-version")
