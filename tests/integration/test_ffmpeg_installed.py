"""Checks the real FFmpeg installed by scripts/setup_ffmpeg.py (T-002)."""

import json
from pathlib import Path

import pytest

from reel.media.ffmpeg_check import check_capabilities

FFMPEG_DIR = Path(__file__).resolve().parents[2] / "tools" / "ffmpeg"
FFMPEG = FFMPEG_DIR / "bin" / "ffmpeg.exe"
FFPROBE = FFMPEG_DIR / "bin" / "ffprobe.exe"

pytestmark = pytest.mark.skipif(
    not (FFMPEG.is_file() and FFPROBE.is_file()),
    reason="FFmpeg not installed; run: .venv\\Scripts\\python scripts\\setup_ffmpeg.py",
)


def test_installed_build_has_required_features():
    report = check_capabilities(FFMPEG, FFPROBE)
    assert report.ok, (report.missing_filters, report.missing_encoders)


def test_installed_version_matches_manifest():
    manifest = json.loads((FFMPEG_DIR / "INSTALL.json").read_text(encoding="utf-8"))
    report = check_capabilities(FFMPEG, FFPROBE)
    assert f"version {manifest['version']}" in report.ffmpeg_version
    assert f"version {manifest['version']}" in report.ffprobe_version
