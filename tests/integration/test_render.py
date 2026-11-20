"""Renderer and loudness normalisation on real FFmpeg output (T-405)."""

import subprocess
from pathlib import Path

import numpy as np
import pytest

from reel.cleaning.cutlist import Cut, Part, Timeline, build_cut_list
from reel.cleaning.framing import Crop
from reel.config.schema import AudioSettings, CutSettings, RenderSettings
from reel.media.ffmpeg import FfmpegCancelled, probe, run_ffmpeg
from reel.media.render import _parse_loudnorm, build_graph, output_size, probe_source, render
from reel.media.testmedia import Part as Scene
from reel.media.testmedia import make_lesson

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "ffmpeg" / "bin"
FFMPEG, FFPROBE = BIN / "ffmpeg.exe", BIN / "ffprobe.exe"
FRAME = 1 / 25
CPU = RenderSettings(video_encoder="libx264", preset="ultrafast")
AUDIO = AudioSettings()


@pytest.fixture(scope="module")
def parts(tmp_path_factory) -> list[Path]:
    folder = tmp_path_factory.mktemp("render")
    return [
        make_lesson(FFMPEG, folder / "part1.mp4", (Scene(20, "moving", "tone"),)),
        make_lesson(FFMPEG, folder / "part2.mp4", (Scene(15, "moving", "noise"),)),
    ]


def cut_list_for(paths: list[Path], cuts: list[Cut]):
    timeline = Timeline([Part(probe(FFPROBE, p).duration_seconds, 25, p.name) for p in paths])
    return build_cut_list(timeline, cuts, CutSettings(padding_seconds=0))


CUTS = [Cut(3, 8, "silence"), Cut(16, 23, "nobody at the board")]  # the second crosses the join
JOINS = [Cut(3, 8, "silence"), Cut(25, 28, "silence")]  # kept: the two files meet at 20 s


def streams(path: Path) -> dict[str, float]:
    output = subprocess.check_output(
        [str(FFPROBE), "-v", "error", "-show_entries", "stream=codec_type,duration",
         "-of", "csv=p=0", str(path)],
        text=True,
    )  # fmt: skip
    return {kind: float(value) for kind, value in (line.split(",") for line in output.split())}


def loudness(path: Path) -> float:
    lines: list[str] = []
    run_ffmpeg(
        FFMPEG,
        ["-v", "info", "-i", path, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        on_stderr=lines.append,
    )
    return float(_parse_loudnorm(lines)["input_i"])


def samples(path: Path) -> np.ndarray:
    raw = subprocess.check_output(
        [str(FFMPEG), "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000",
         "-f", "f32le", "-"],
    )  # fmt: skip
    return np.frombuffer(raw, np.float32)


def test_the_cut_list_is_rendered_exactly(parts, tmp_path):
    cut_list = cut_list_for(parts, CUTS)
    progress = []
    result = render(
        FFMPEG,
        FFPROBE,
        parts,
        cut_list,
        tmp_path / "cleaned.mp4",
        CPU,
        AUDIO,
        on_progress=lambda done, share: progress.append(share),
    )
    assert result.encoder == "libx264" and result.path.is_file()
    assert not list(tmp_path.glob("*.part"))
    durations = streams(result.path)
    assert durations["video"] == pytest.approx(cut_list.final_duration, abs=FRAME)
    assert durations["audio"] == pytest.approx(durations["video"], abs=FRAME)
    info = probe(FFPROBE, result.path)
    assert (info.width, info.height) == (320, 240)  # never scaled up (D-037)
    assert info.video_codec == "h264" and info.audio_codec == "aac"
    assert loudness(result.path) == pytest.approx(AUDIO.loudness_target_lufs, abs=1)
    assert result.loudness["result"]["normalization_type"] in {"linear", "dynamic"}
    assert progress and progress[-1] == pytest.approx(1, abs=0.05)
    assert all(b >= a for a, b in zip(progress, progress[1:], strict=False))


def test_no_clicks_at_the_joins(parts, tmp_path):
    cut_list = cut_list_for(parts, JOINS)
    result = render(FFMPEG, FFPROBE, parts, cut_list, tmp_path / "c.mp4", CPU, AUDIO)
    audio = samples(result.path)
    peak = np.abs(audio).max()
    joins, clean = [], 0.0
    for segment in cut_list.kept[:-1]:
        clean += segment.duration
        joins.append(clean)
    assert len(joins) == 3 and joins[1] == pytest.approx(15)  # two cuts, the file join
    for seconds in joins:
        centre = round(seconds * 48000)
        window = audio[centre - 48 : centre + 48]  # ±1 ms around the join
        assert np.abs(window).max() < 0.15 * peak, f"click at {seconds:.3f} s"


def test_tall_pictures_are_scaled_down_keeping_the_shape(tmp_path):
    source = make_lesson(
        FFMPEG, tmp_path / "big.mp4", (Scene(2, "moving", "tone"),), size="1280x720"
    )
    cut_list = cut_list_for([source], [])
    settings = RenderSettings(video_encoder="libx264", preset="ultrafast", max_height=360)
    result = render(FFMPEG, FFPROBE, [source], cut_list, tmp_path / "small.mp4", settings, AUDIO)
    info = probe(FFPROBE, result.path)
    assert (info.width, info.height) == (640, 360)


@pytest.mark.parametrize(
    ("size", "limit", "expected"),
    [
        ((3840, 2160), 1080, (1920, 1080)),
        ((2160, 3840), 1080, (1080, 1920)),  # portrait: the shorter side counts
        ((1280, 720), 1080, (1280, 720)),  # never up
        ((321, 241), 1080, (320, 240)),  # even numbers for H.264
    ],
)
def test_output_size(size, limit, expected):
    assert output_size(*size, limit) == expected


def test_quick_sync_or_cpu_fallback(parts, tmp_path):
    cut_list = cut_list_for(parts, CUTS)
    result = render(
        FFMPEG, FFPROBE, parts, cut_list, tmp_path / "auto.mp4", RenderSettings(), AUDIO
    )
    assert result.encoder in {"h264_qsv", "libx264"}
    durations = streams(result.path)
    assert durations["video"] == pytest.approx(cut_list.final_duration, abs=FRAME)
    assert durations["audio"] == pytest.approx(durations["video"], abs=FRAME)


def test_silent_audio_is_not_normalised_but_rendered(tmp_path):
    source = make_lesson(FFMPEG, tmp_path / "quiet.mp4", (Scene(4, "moving", "silence"),))
    cut_list = cut_list_for([source], [])
    result = render(FFMPEG, FFPROBE, [source], cut_list, tmp_path / "q.mp4", CPU, AUDIO)
    assert result.loudness["skipped"] == "silent" and result.path.is_file()


def test_cancellation_leaves_no_file(parts, tmp_path):
    def cancel_at_once(kill):
        kill()

    with pytest.raises(FfmpegCancelled):
        render(
            FFMPEG,
            FFPROBE,
            parts,
            cut_list_for(parts, CUTS),
            tmp_path / "x.mp4",
            CPU,
            AUDIO,
            on_cancel=cancel_at_once,
        )
    assert not list(tmp_path.glob("x.mp4*"))


def test_the_graph_trims_each_part_and_joins_in_order(parts):
    cut_list = cut_list_for(parts, CUTS)
    videos = [probe_source(FFPROBE, p) for p in parts]
    graph = build_graph(cut_list, videos, (320, 240), gpu=False, fade_seconds=0.03)
    assert graph.count("trim=start=") == 2 * len(cut_list.kept)  # trim and atrim
    assert "[0:v]scale=320:240" in graph and "split=2" in graph
    assert "concat=n=3:v=1:a=1[v][ca]" in graph
    gpu = build_graph(cut_list, videos, (320, 240), gpu=True, fade_seconds=0.03)
    assert "scale_qsv=w=320:h=240" in gpu


# --- the board framing (D-058) ----------------------------------------------------------------


@pytest.fixture(scope="module")
def two_tone(tmp_path_factory) -> Path:
    """Left half black, right half white, with a tone: a crop of the right half is all white."""
    path = tmp_path_factory.mktemp("crop") / "two_tone.mp4"
    subprocess.run(
        [str(FFMPEG), "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=black:s=640x360:r=25:d=3,"
                               "drawbox=x=320:y=0:w=320:h=360:color=white:t=fill",
         "-f", "lavfi", "-i", "sine=f=440:d=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )  # fmt: skip
    return path


def middle_brightness(path: Path) -> float:
    raw = subprocess.check_output(
        [str(FFMPEG), "-v", "error", "-ss", "1.5", "-i", str(path), "-frames:v", "1",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
    )  # fmt: skip
    return float(np.frombuffer(raw, np.uint8).mean())


def test_the_graph_crops_before_scaling(parts):
    cut_list = cut_list_for(parts, CUTS)
    videos = [probe_source(FFPROBE, p) for p in parts]
    crops = [Crop(10, 20, 160, 90), None]
    cpu = build_graph(cut_list, videos, (320, 180), gpu=False, fade_seconds=0.03, crops=crops)
    assert "[0:v]crop=160:90:10:20,scale=320:180" in cpu and "[1:v]scale=320:180" in cpu
    gpu = build_graph(cut_list, videos, (320, 180), gpu=True, fade_seconds=0.03, crops=crops)
    assert "[0:v]vpp_qsv=cx=10:cy=20:cw=160:ch=90:w=320:h=180" in gpu
    assert "[1:v]scale_qsv=w=320:h=180" in gpu


@pytest.mark.parametrize("settings", [CPU, RenderSettings()], ids=["cpu", "auto"])
def test_the_crop_is_rendered(two_tone, tmp_path, settings):
    cut_list = cut_list_for([two_tone], [])
    plain = render(FFMPEG, FFPROBE, [two_tone], cut_list, tmp_path / "plain.mp4", settings, AUDIO)
    framed = render(
        FFMPEG, FFPROBE, [two_tone], cut_list, tmp_path / "framed.mp4", settings, AUDIO,
        crops=[Crop(320, 0, 320, 180)],
    )  # fmt: skip
    assert (framed.width, framed.height) == (plain.width, plain.height) == (640, 360)
    assert framed.details["crops"] == [[320, 0, 320, 180]]
    assert 100 < middle_brightness(plain.path) < 160  # half black, half white
    assert middle_brightness(framed.path) > 220  # only the white half, zoomed
    assert streams(framed.path)["video"] == pytest.approx(cut_list.final_duration, abs=FRAME)
