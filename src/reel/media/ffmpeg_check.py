"""Check that an FFmpeg build has every filter and encoder Reel needs.

Used by ``scripts/setup_ffmpeg.py`` right after installation. Later tasks use it to
verify whatever FFmpeg the configuration points to, for example a system FFmpeg on a
Linux server.
"""

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Filters used by the pipeline and by the synthetic test media.
REQUIRED_FILTERS: tuple[str, ...] = (
    "silencedetect",  # FR-6: silences
    "freezedetect",  # D-014: static picture
    "loudnorm",  # FR-11: loudness normalisation
    "ebur128",  # loudness measurement in tests
    "thumbnail",  # FR-18: representative frame
    "trim",  # FR-10: render from the cut list
    "atrim",
    "setpts",
    "asetpts",
    "concat",
    "afade",
    "aresample",
    "scale",
    "pad",
    "fps",
    "testsrc2",  # T-201: synthetic test media sources
    "sine",
    "anoisesrc",
    "anullsrc",
)
REQUIRED_ENCODERS: tuple[str, ...] = ("libx264", "aac")

# " T.C afade             A->A       Fade in/out input audio."
_FILTER_LINE = re.compile(r"^\s*\S{2,4}\s+([a-z0-9_]+)\s+[AVN|]+->[AVN|]+\s")
# " V....D libx264              libx264 H.264 / AVC / ..."
_ENCODER_LINE = re.compile(r"^\s*[VAS][A-Z.]{5}\s+([A-Za-z0-9_\-]+)\s")

Runner = Callable[..., str]


class FfmpegError(RuntimeError):
    """FFmpeg or ffprobe is missing or cannot be run."""


@dataclass(frozen=True)
class CapabilityReport:
    """Versions and missing features of one FFmpeg/ffprobe pair."""

    ffmpeg_version: str
    ffprobe_version: str
    missing_filters: tuple[str, ...] = ()
    missing_encoders: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing_filters and not self.missing_encoders


def parse_filters(output: str) -> set[str]:
    """Return the filter names listed by ``ffmpeg -filters``."""
    return {m.group(1) for line in output.splitlines() if (m := _FILTER_LINE.match(line))}


def parse_encoders(output: str) -> set[str]:
    """Return the encoder names listed by ``ffmpeg -encoders`` (after the ``------`` line)."""
    names: set[str] = set()
    in_list = False
    for line in output.splitlines():
        if line.strip().startswith("------"):
            in_list = True
        elif in_list and (m := _ENCODER_LINE.match(line)):
            names.add(m.group(1))
    return names


def run_tool(binary: Path, *args: str) -> str:
    """Run ``binary -hide_banner *args`` and return its standard output."""
    try:
        proc = subprocess.run(
            [str(binary), "-hide_banner", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise FfmpegError(f"{binary} was not found") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FfmpegError(f"cannot run {binary}: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip()[:500]
        raise FfmpegError(
            f"{binary.name} {' '.join(args)} failed with exit code {proc.returncode}: {detail}"
        )
    return proc.stdout


def check_capabilities(ffmpeg: Path, ffprobe: Path, runner: Runner = run_tool) -> CapabilityReport:
    """Run both tools and report their versions and any missing filters or encoders."""
    filters = parse_filters(runner(ffmpeg, "-filters"))
    encoders = parse_encoders(runner(ffmpeg, "-encoders"))
    return CapabilityReport(
        ffmpeg_version=_first_line(runner(ffmpeg, "-version")),
        ffprobe_version=_first_line(runner(ffprobe, "-version")),
        missing_filters=tuple(name for name in REQUIRED_FILTERS if name not in filters),
        missing_encoders=tuple(name for name in REQUIRED_ENCODERS if name not in encoders),
    )


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""
