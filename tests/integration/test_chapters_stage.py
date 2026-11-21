"""Stage ``chapters`` end to end with a scripted LLM and a synthetic cleaned video (T-504)."""

from pathlib import Path

import pytest
from sqlalchemy import select

from reel.config import load_config
from reel.db import (
    Job,
    JobStatus,
    Recording,
    Stage,
    TranscriptKind,
    VideoMetadata,
    init_database,
    open_database,
)
from reel.llm import FakeLLM
from reel.media.ffmpeg import probe
from reel.media.testmedia import Part as Scene
from reel.media.testmedia import make_lesson
from reel.metadata.chapters import NOT_GENERATED
from reel.queue import JobQueue, StageRegistry, Worker, create_job
from reel.stages.chapters import METADATA_FILE, SPEECH_UNCLEAR, THUMBNAIL_FILE, ChaptersStage
from reel.stages.cleaning import CLEANED_FILE
from reel.stt import Segment, Transcription, save_transcript

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "ffmpeg" / "bin"

CONFIG = f"""
[paths]
ffmpeg = "{(BIN / "ffmpeg.exe").as_posix()}"
ffprobe = "{(BIN / "ffprobe.exe").as_posix()}"
prompts_dir = "{(ROOT / "config" / "prompts").as_posix()}"

[metadata]
title_template = "{{group}} · Դաս {{lesson}}"
description_footer = "{{group}}, դաս {{lesson}}"

[workers]
max_attempts = 1
idle_poll_seconds = 0.02
"""
DICT = "Dictionary-ի մեթոդները՝ keys և values, dictionary-ն պահում է զույգեր։"
LOOP = "For ցիկլը անցնում է list-ի տարրերով, ամեն քայլին for ցիկլը վերցնում է հաջորդը։"
FUNC = "Ֆունկցիան սահմանում ենք def բառով, ֆունկցիան վերադարձնում է return արժեքը։"


def clean_transcript() -> Transcription:
    """30 minutes: dictionaries, loops, then «հիմա անցնենք» functions at 20:00."""
    segments = []
    for t in range(0, 1800, 20):
        text = (
            "Լավ, հիմա անցնենք ֆունկցիաներին։"
            if t == 1200
            else DICT
            if t < 540
            else LOOP
            if t < 1200
            else FUNC
        )
        segments.append(Segment(t, t + 18, text))
    return Transcription("hy", tuple(segments), "fake")


def scripted(prompt, schema):
    if "title" in schema.model_fields:
        return {"title": prompt.split("\n- ")[1].split("\n")[0]}
    if "def բառով" in prompt:
        return {"label": "Ֆունկցիաների սահմանում", "words": ["ֆունկցիան", "def"]}
    if "For ցիկլը" in prompt:
        return {"label": "For ցիկլ list-ի վրա", "words": ["for ցիկլը"]}
    return {"label": "Dictionary-ի մեթոդներ", "words": ["dictionary-ն"]}


@pytest.fixture(scope="module")
def cleaned_video(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("chapters")
    return make_lesson(
        BIN / "ffmpeg.exe", folder / "cleaned.mp4", (Scene(1800, "moving", "tone"),),
        size="160x120", fps=2,
    )  # fmt: skip


@pytest.fixture
def setup(project, write_config, cleaned_video):
    opened = []

    def setup(
        *, group: str | None = "Python 12", lesson: int | None = 14,
        transcript: Transcription | None = None, metadata: str = "",
    ):  # fmt: skip
        write_config(CONFIG.replace("[metadata]\n", "[metadata]\n" + metadata))
        cfg = load_config(project / "config" / "reel.toml")
        init_database(cfg.paths.database_file)
        db = open_database(cfg.paths.database_file)
        opened.append(db)
        with db.transaction() as s:
            recording = Recording(source_path="lesson.mp4", size_bytes=1)
            recording.group_name, recording.lesson_number = group, lesson
            s.add(recording)
            job_id = create_job(s, recording, stage=Stage.CHAPTERS).id
            save_transcript(s, job_id, TranscriptKind.CLEAN, transcript or clean_transcript())
        job_dir = cfg.paths.data_dir / "jobs" / str(job_id)
        job_dir.mkdir(parents=True)
        (job_dir / CLEANED_FILE).write_bytes(cleaned_video.read_bytes())
        return cfg, db, job_id, job_dir

    yield setup
    for db in opened:
        db.dispose()


def work(cfg, db, answers=None) -> list:
    answers = [scripted] * 100 if answers is None else answers
    registry = StageRegistry([ChaptersStage(lambda cfg: FakeLLM(answers))])
    attention: list = []
    worker = Worker(db, registry, lambda: cfg, notifier=attention.append)
    worker.start(until_idle=True)
    assert worker.join(120)
    return attention


def load(db, job_id):
    with db.transaction() as s:
        job = s.get(Job, job_id)
        row = s.scalars(select(VideoMetadata).where(VideoMetadata.job_id == job_id)).one_or_none()
        s.expunge_all()
    return job, row


def test_title_description_chapters_and_thumbnail_are_stored(setup):
    cfg, db, job_id, job_dir = setup()
    assert work(cfg, db) == []
    job, row = load(db, job_id)
    assert (job.stage, job.status, job.flags) == (Stage.CONSENT, JobStatus.QUEUED, [])
    assert row.title == "Python 12 · Դաս 14"
    assert row.chapters == [
        {"start": 0.0, "title": "Dictionary-ի մեթոդներ"},
        {"start": 540.0, "title": "For ցիկլ list-ի վրա"},
        {"start": 1200.0, "title": "Ֆունկցիաների սահմանում"},
    ]
    assert row.summary.startswith("Թեմաներ՝ Dictionary-ի մեթոդներ")
    assert "0:00 Dictionary-ի մեթոդներ\n9:00 For ցիկլ list-ի վրա\n20:00 Ֆունկցիաների" in (
        row.description
    )
    assert row.description.endswith("Python 12, դաս 14")
    thumbnail = probe(BIN / "ffprobe.exe", row.thumbnail_path)
    assert row.thumbnail_path == str(job_dir / THUMBNAIL_FILE)
    assert (thumbnail.width, thumbnail.height) == (1280, 720)
    assert (job_dir / METADATA_FILE).is_file()
    assert row.generator["topic"] in {c["title"] for c in row.chapters}


def test_missing_identification_holds_then_completes_after_release(setup):
    cfg, db, job_id, _ = setup(group=None, lesson=None)
    (attention,) = work(cfg, db)
    job, row = load(db, job_id)
    assert (job.status, job.hold_reason, row) == (JobStatus.ON_HOLD, "identification", None)
    assert "group and lesson number" in job.hold_message
    assert attention.kind == "held" or "hold" in attention.kind
    # Staff set the group and the lesson number and release the job (UJ-3).
    with db.transaction() as s:
        recording = s.get(Job, job_id).recording
        recording.group_name, recording.lesson_number = "Python 12", 3
    JobQueue(db).release(job_id, "staff")
    work(cfg, db)
    job, row = load(db, job_id)
    assert (job.stage, job.status) == (Stage.CONSENT, JobStatus.QUEUED)
    assert row.title == "Python 12 · Դաս 3"


def test_an_unknown_group_is_held_too(setup):
    cfg, db, job_id, _ = setup(group="Nobody 99")
    work(cfg, db)
    job, _ = load(db, job_id)
    assert (job.status, job.hold_reason) == (JobStatus.ON_HOLD, "identification")
    assert 'The group "Nobody 99" is not in config/groups.toml' in job.hold_message


# --- T-1012: the speech is not clear -------------------------------------------------------

SILENT = Transcription("hy", (), "fake")  # the microphone was off: nothing was recognised


def test_unclear_speech_holds_for_staff_with_the_title_ready(setup):
    cfg, db, job_id, job_dir = setup(transcript=SILENT)
    (attention,) = work(cfg, db, answers=[])  # the LLM is not needed and not asked
    job, row = load(db, job_id)
    assert (job.stage, job.status, job.hold_reason) == (
        Stage.CHAPTERS,
        JobStatus.ON_HOLD,
        SPEECH_UNCLEAR,
    )
    assert job.hold_message.startswith("Տեսանյութում խոսքը պարզ չէ (0 ճանաչված բառ 30 րոպեում)")
    assert set(job.flags) == {NOT_GENERATED, SPEECH_UNCLEAR}
    assert "hold" in attention.kind or attention.kind == "held"
    # Everything else is ready, so publishing it as it is only needs the staff's decision.
    assert row.title == "Python 12 · Դաս 14" and row.chapters == []
    assert row.description == "Python 12, դաս 14"
    assert row.generator["chapter_details"]["words"] == 0
    assert (job_dir / METADATA_FILE).is_file() and (job_dir / THUMBNAIL_FILE).is_file()
    JobQueue(db).release(job_id, "staff", advance=True)
    job, _ = load(db, job_id)
    assert (job.stage, job.status) == (Stage.CONSENT, JobStatus.QUEUED)


def test_little_speech_with_chapters_found_needs_no_decision(setup):
    sparse = tuple(s for s in clean_transcript().segments if s.start % 60 == 0)  # ~11 words/min
    cfg, db, job_id, _ = setup(transcript=Transcription("hy", sparse, "fake"))
    assert work(cfg, db) == []
    job, row = load(db, job_id)
    assert (job.stage, job.status) == (Stage.CONSENT, JobStatus.QUEUED)
    assert len(row.chapters) == 3 and row.generator["chapter_details"]["words_per_minute"] < 20


def test_the_check_can_be_turned_off(setup):
    cfg, db, job_id, _ = setup(transcript=SILENT, metadata="min_words_per_minute = 0\n")
    assert work(cfg, db, answers=[]) == []
    job, _ = load(db, job_id)
    assert (job.stage, job.status, job.flags) == (Stage.CONSENT, JobStatus.QUEUED, [NOT_GENERATED])


def test_a_video_only_processed_needs_no_group_in_groups_toml(setup):
    cfg, db, job_id, _ = setup(group="Իմ խումբը")
    with db.transaction() as s:
        s.get(Job, job_id).recording.publish = False  # a user's upload (T-1013)
    assert work(cfg, db) == []
    job, row = load(db, job_id)
    assert (job.stage, job.status) == (Stage.CONSENT, JobStatus.QUEUED)
    assert row.title == "Իմ խումբը · Դաս 14" and len(row.chapters) == 3


def test_unclear_speech_of_a_video_only_processed_asks_to_finish(setup):
    cfg, db, job_id, _ = setup(transcript=SILENT)
    with db.transaction() as s:
        s.get(Job, job_id).recording.publish = False
    work(cfg, db, answers=[])
    job, _ = load(db, job_id)
    assert job.hold_reason == SPEECH_UNCLEAR and "ավարտել մշակումն այսպես" in job.hold_message
