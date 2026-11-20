"""Stage ``clean`` end to end on synthetic media with a scripted raw transcript (T-406)."""

import json
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select

from reel.config import load_config
from reel.db import (
    CutList,
    Job,
    JobEvent,
    JobStatus,
    Recording,
    Stage,
    StageRun,
    TranscriptKind,
    init_database,
    open_database,
)
from reel.media.ffmpeg import probe
from reel.media.testmedia import Part as Scene
from reel.media.testmedia import make_lesson
from reel.queue import StageRegistry, Worker, create_job
from reel.stages.cleaning import CLEANED_FILE, CUT_LIST_FILE, SUMMARY_FILE, CleanStage
from reel.stt import Segment, Transcription, load_transcript, save_transcript

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "ffmpeg" / "bin"
FFMPEG = BIN / "ffmpeg.exe"

CONFIG = f"""
[paths]
ffmpeg = "{(BIN / "ffmpeg.exe").as_posix()}"
ffprobe = "{(BIN / "ffprobe.exe").as_posix()}"
prompts_dir = "{(ROOT / "config" / "prompts").as_posix()}"

[cleaning.boundaries]
use_llm = false

[render]
video_encoder = "libx264"
preset = "ultrafast"

[workers]
max_attempts = 1
idle_poll_seconds = 0.02
"""

# chatter | lesson | nobody at the board, silent | lesson | chatter
SCENES = (
    Scene(20, "moving", "noise"),
    Scene(30, "moving", "tone"),
    Scene(15, "static", "silence"),
    Scene(25, "moving", "tone"),
    Scene(10, "moving", "noise"),
)
RAW = Transcription(
    "hy",
    (
        Segment(5, 8, "Լսու՞մ եք ինձ"),
        Segment(20, 23, "Բարև ձեզ"),
        Segment(23.5, 27, "Այսօր կխոսենք ցիկլերի մասին"),
        Segment(30, 40, "Հիմա for ցիկլը"),
        Segment(45, 53, "Վերջին օրինակը"),  # reaches into the cut part: clipped
        Segment(57, 60, "Ոչ ոք չկա"),  # inside the cut part: dropped
        Segment(70, 80, "while ցիկլը"),
        Segment(86, 89.5, "Այսօրվա համար այսքանը"),
        Segment(92, 95, "Արամ, մի հարց ունեմ"),
    ),
    "fake",
)


def person_if_busy(frame: np.ndarray, content_height: int) -> float:
    """The static scene is one flat colour: nobody at the board."""
    return 0.9 if frame[:content_height].std() > 10 else 0.0


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, list[Path]]:
    folder = tmp_path_factory.mktemp("clean-media")
    return {
        "one": [make_lesson(FFMPEG, folder / "lesson.mp4", SCENES)],
        "two": [
            make_lesson(FFMPEG, folder / "part1.mp4", SCENES[:3]),
            make_lesson(FFMPEG, folder / "part2.mp4", SCENES[3:]),
        ],
    }


@pytest.fixture
def run_stage(project, write_config):
    opened = []

    def run(files: list[Path], *, raw: Transcription | None = RAW):
        write_config(CONFIG)
        cfg = load_config(project / "config" / "reel.toml")
        init_database(cfg.paths.database_file)
        db = open_database(cfg.paths.database_file)
        opened.append(db)
        with db.transaction() as s:
            recording = Recording(source_path=str(files[0]), size_bytes=1, group_name="Python 12")
            if len(files) > 1:
                recording.media_info = {"parts": [{"path": str(f)} for f in files]}
            s.add(recording)
            job_id = create_job(s, recording, stage=Stage.CLEAN).id
            if raw is not None:
                save_transcript(s, job_id, TranscriptKind.RAW, raw)
        registry = StageRegistry()
        registry.register(CleanStage(detector=person_if_busy))
        attention = []
        worker = Worker(db, registry, lambda: cfg, notifier=attention.append)
        worker.start(until_idle=True)
        assert worker.join(300)
        with db.transaction() as s:
            job = s.get(Job, job_id)
            (run,) = s.scalars(select(StageRun).where(StageRun.job_id == job_id)).all()
            cut_lists = s.scalars(select(CutList).where(CutList.job_id == job_id)).all()
            clean = load_transcript(s, job_id, TranscriptKind.CLEAN)
            events = s.scalars(select(JobEvent.message).where(JobEvent.job_id == job_id)).all()
            run.metrics["events"] = list(events)
            s.expunge_all()
        return cfg.paths.data_dir / "jobs" / str(job_id), job, run, cut_lists, clean, attention

    yield run
    for db in opened:
        db.dispose()


@pytest.mark.parametrize("kind", ["one", "two"])
def test_the_lesson_is_cleaned_and_the_transcript_follows(media, run_stage, kind):
    job_dir, job, run, (cut_list,), clean, attention = run_stage(media[kind])
    assert (job.stage, job.status, attention) == (Stage.CHAPTERS, JobStatus.QUEUED, [])
    assert job.flags == []  # start and end were found
    # The cut list: before the greeting, the empty silent part, after the closing.
    removed = [(s["start"], s["end"]) for s in cut_list.segments if s["action"] == "remove"]
    assert removed[0] == (0, pytest.approx(19.5, abs=0.04))  # 0.5 s before «Բարև ձեզ» (± a frame)
    assert removed[-1] == (pytest.approx(90.0, abs=0.04), 100)  # 0.5 s kept after the closing
    middle = [r for r in removed[1:-1] if r[0] > 50 and r[1] < 65]
    assert middle, f"the empty part 50-65 s was not cut: {removed}"
    assert (job_dir / CUT_LIST_FILE).is_file() and (job_dir / SUMMARY_FILE).is_file()
    if kind == "two":
        assert {s["part"] for s in cut_list.segments} == {0, 1}
    # The video.
    video = probe(BIN / "ffprobe.exe", job_dir / CLEANED_FILE)
    assert video.duration_seconds == pytest.approx(cut_list.final_duration_seconds, abs=0.1)
    # The clean transcript: ordered, inside the video, the cut-out line dropped.
    texts = [s.text for s in clean.segments]
    assert texts[0] == "Բարև ձեզ" and "Ոչ ոք չկա" not in texts and texts[-1].startswith("Այսօրվա")
    assert "Լսու՞մ եք ինձ" not in texts and "Արամ, մի հարց ունեմ" not in texts
    starts = [s.start for s in clean.segments]
    assert starts == sorted(starts)
    assert all(0 <= s.start <= s.end <= cut_list.final_duration_seconds for s in clean.segments)
    assert clean.segments[0].start == pytest.approx(0.5, abs=0.04)  # the kept 0.5 s (± a frame)
    # "X cut to Y" is recorded.
    assert run.metrics["original_seconds"] == pytest.approx(100, abs=0.1)
    assert run.metrics["final_seconds"] == pytest.approx(cut_list.final_duration_seconds)
    assert any(" cut to " in m for m in run.metrics["events"])
    assert run.metrics["parts"] == len(media[kind])
    summary = json.loads((job_dir / SUMMARY_FILE).read_text("utf-8"))
    assert summary["boundaries"]["start"]["method"] == "greeting + topic"
    # the board framing (D-058) ran on the same pictures, one result per part
    assert [f["part"] for f in summary["framing"]] == list(range(len(media[kind])))
    assert all(f["crop_note"] for f in summary["framing"])
    assert len(run.metrics["framing"]) == len(media[kind])


def test_without_a_raw_transcript_the_stage_fails_at_once(media, run_stage):
    _dir, job, run, cut_lists, _clean, (attention,) = run_stage(media["one"], raw=None)
    assert (job.status, cut_lists) == (JobStatus.FAILED, [])
    assert "re-run the job from Transcribed" in job.last_error


def test_a_lesson_without_its_start_and_end_is_kept_whole_and_flagged(media, run_stage):
    plain = Transcription("hy", (Segment(30, 40, "Հիմա for ցիկլը"),), "fake")
    _dir, job, run, (cut_list,), clean, _ = run_stage(media["one"], raw=plain)
    assert set(job.flags) == {"lesson_start_not_found", "lesson_end_not_found"}
    removed = [s for s in cut_list.segments if s["action"] == "remove"]
    assert all(s["start"] > 0 and s["end"] < 100 for s in removed)  # only the empty part
