"""Board check on real FFmpeg output (T-403, D-040).

Synthetic videos stand in for the camera: red = "somebody at the board", grey = empty. A fake
detector reads the colour, so these tests check sampling, picture times, decoding modes and
cancellation; the real person model is checked on an empty picture when it is installed.
"""

from pathlib import Path

import numpy as np
import pytest

from reel.cleaning.presence import (
    INPUT_SIZE,
    ModelMissing,
    PersonDetector,
    analyze_board,
    model_path,
)
from reel.config.schema import EmptyBoardSettings
from reel.media.ffmpeg import FfmpegCancelled, run_ffmpeg

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "ffmpeg" / "bin"
FFMPEG, FFPROBE = BIN / "ffmpeg.exe", BIN / "ffprobe.exe"
MODEL = model_path(ROOT / "models", "yolox_tiny")
SCENES = (("red", 20), ("gray", 25), ("red", 15))  # empty from 20 s to 45 s


def make_video(target: Path, gop: int) -> Path:
    inputs, labels = [], ""
    for index, (colour, seconds) in enumerate(SCENES):
        inputs += ["-f", "lavfi", "-i", f"color=c={colour}:s=320x240:r=24:d={seconds}"]
        labels += f"[{index}:v]"
    run_ffmpeg(
        FFMPEG,
        [
            "-y", *inputs,
            "-filter_complex", f"{labels}concat=n={len(SCENES)}:v=1:a=0[v]", "-map", "[v]",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", gop, "-pix_fmt", "yuv420p", target,
        ],
    )  # fmt: skip
    return target


@pytest.fixture(scope="module")
def videos(tmp_path_factory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp("board")
    return {
        "keyframes": make_video(folder / "dense.mp4", 12),  # a keyframe every 0.5 s
        "fps": make_video(folder / "sparse.mp4", 600),  # one keyframe only
    }


def by_colour(frame: np.ndarray, content_height: int) -> float:
    """Somebody is there when the picture is red (BGR)."""
    blue, green, red = frame[:content_height].reshape(-1, 3).mean(axis=0)
    return 0.9 if red > 150 and blue < 80 and green < 80 else 0.0


@pytest.mark.parametrize(
    ("kind", "hwaccel"), [("keyframes", "none"), ("keyframes", "auto"), ("fps", "none")]
)
def test_the_empty_part_is_found_and_shortened(videos, kind, hwaccel):
    settings = EmptyBoardSettings(hwaccel=hwaccel)
    progress = []
    result = analyze_board(
        FFMPEG,
        FFPROBE,
        videos[kind],
        settings,
        detector=by_colour,
        on_progress=lambda done, share: progress.append(share),
    )
    assert result.mode.startswith(kind)
    times = [s.seconds for s in result.samples]
    assert times == sorted(times) and times[0] < 1 and times[-1] > 58
    assert len(times) >= 59  # at least one picture per second
    [empty] = result.empty
    assert empty.start == pytest.approx(20, abs=1) and empty.end == pytest.approx(45, abs=1)
    [cut] = result.removals
    kept = (cut.start - empty.start) + (empty.end - cut.end)
    assert kept == pytest.approx(settings.keep_seconds)
    assert progress and all(0 <= share <= 1 for share in progress)


def test_cancellation_stops_ffmpeg(videos):
    kills = []

    def detector(frame: np.ndarray, content_height: int) -> float:
        if kills:
            kills[0]()  # staff pressed "cancel" while the check was running
        return 0.0

    with pytest.raises(FfmpegCancelled):
        analyze_board(
            FFMPEG,
            FFPROBE,
            videos["fps"],
            EmptyBoardSettings(hwaccel="none"),
            detector=detector,
            on_cancel=kills.append,
        )


def test_a_missing_model_says_how_to_install_it(tmp_path):
    with pytest.raises(ModelMissing, match="setup_person_model.py"):
        PersonDetector(model_path(tmp_path, "yolox_tiny"), min_height=0.08)


@pytest.mark.skipif(not MODEL.is_file(), reason="person model not installed")
def test_the_real_model_sees_nobody_on_an_empty_board(videos):
    detector = PersonDetector(MODEL, min_height=0.08)
    grey = np.full((INPUT_SIZE, INPUT_SIZE, 3), 128, np.uint8)
    assert detector(grey, 312) < 0.1
    result = analyze_board(
        FFMPEG, FFPROBE, videos["keyframes"], EmptyBoardSettings(), models_dir=ROOT / "models"
    )
    assert all(s.score < EmptyBoardSettings().person_score for s in result.samples)
    [cut] = result.removals  # plain colours: nobody for the whole minute
    assert cut.end - cut.start == pytest.approx(60 - 5, abs=1.5)
