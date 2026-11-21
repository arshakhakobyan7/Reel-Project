"""Stage ``transcribe`` (T-302) with the fake transcriber; no speech model needed."""

import json
import logging
import time
import wave

import pytest
from sqlalchemy import select

from reel.config import load_config
from reel.config.schema import TERM_EXAMPLE
from reel.db import (
    Job,
    JobStatus,
    Recording,
    Stage,
    StageRun,
    TranscriptKind,
    init_database,
    open_database,
)
from reel.queue import StageRegistry, Worker, create_job
from reel.stages.transcribing import TranscribeStage
from reel.stt import FakeTranscriber, Segment, load_transcript
from reel.stt.whisper import _drop_repeats, make_transcriber

CONFIG = """
[stt]
initial_prompt = "Ծրագրավորում"
model = "{model}"
{stt_extra}

[workers]
max_attempts = 2
backoff_base_seconds = 0.01
backoff_factor = 1.0
idle_poll_seconds = 0.02
"""


class SlowFake(FakeTranscriber):
    """Takes a moment per segment, so heartbeats see the progress."""

    def transcribe(self, audio, *, on_progress=None, **kwargs):
        def slow(done):
            on_progress(done)
            time.sleep(0.01)

        return super().transcribe(audio, on_progress=slow, **kwargs)


@pytest.fixture
def make(project, write_config):
    opened = []

    def make(factory, *, folder="Python 12", audio_seconds=600.0, model="tiny", stt_extra=""):
        write_config(CONFIG.format(model=model, stt_extra=stt_extra))
        cfg = load_config(project / "config" / "reel.toml")
        init_database(cfg.paths.database_file)
        db = open_database(cfg.paths.database_file)
        opened.append(db)
        with db.transaction() as s:
            recording = Recording(
                source_path=str(project / "inbox" / folder / "L1.mp4"), size_bytes=1
            )
            recording.group_name = "Python 12" if folder == "Python 12" else None
            s.add(recording)
            job_id = create_job(s, recording, stage=Stage.TRANSCRIBE).id
        if audio_seconds is not None:
            path = cfg.paths.data_dir / "jobs" / str(job_id) / "audio.wav"
            path.parent.mkdir(parents=True)
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(1000)
                audio.writeframes(b"\0\0" * int(audio_seconds * 1000))
        registry = StageRegistry()
        registry.register(TranscribeStage(factory))
        attention = []
        worker = Worker(
            db, registry, lambda: cfg, notifier=attention.append, heartbeat_seconds=0.005
        )
        worker.start(until_idle=True)
        assert worker.join(60)
        with db.transaction() as s:
            job = s.get(Job, job_id)
            runs = s.scalars(select(StageRun).where(StageRun.job_id == job_id)).all()
            transcript = load_transcript(s, job_id, TranscriptKind.RAW)
            s.expunge_all()
        return cfg, job, runs, transcript, attention

    yield make
    for db in opened:
        db.dispose()


def test_the_transcript_is_stored_and_the_job_moves_on(make, caplog):
    fake = SlowFake()
    with caplog.at_level(logging.INFO):
        cfg, job, (run,), transcript, attention = make(lambda cfg: fake)
    assert (job.stage, job.status, job.flags, attention) == (Stage.CLEAN, JobStatus.QUEUED, [], [])
    assert transcript.segments[2].text == "Բարև ձեզ, սկսում ենք։"
    assert transcript.details["seconds"] >= 0 and transcript.details["realtime_factor"] is not None
    stored = json.loads(
        (cfg.paths.data_dir / "jobs" / str(job.id) / "transcript.raw.json").read_text("utf-8")
    )
    assert stored["segments"][2]["text"] == "Բարև ձեզ, սկսում ենք։"
    # The prompt: the example sentence, the group's vocabulary, then [stt] initial_prompt.
    prompt = fake.calls[0]["prompt"]
    assert prompt.startswith(TERM_EXAMPLE + " Python, list") and prompt.endswith(", Ծրագրավորում")
    assert fake.calls[0]["language"] == "hy"
    assert run.metrics["segments"] == len(transcript.segments) and run.metrics["words"] > 0
    progress = run.metrics["progress"]  # saved by a heartbeat
    assert progress["total"] == 600 and 0 < progress["fraction"] <= 1
    assert "transcribed 100% (600 of 600 s)" in caplog.text


def test_a_job_without_a_known_group_uses_only_the_general_prompt(make):
    fake = FakeTranscriber()
    make(lambda cfg: fake, folder="Unknown")
    assert fake.calls[0]["prompt"] == TERM_EXAMPLE + " Ծրագրավորում"


def test_missing_audio_fails_at_once(make):
    _cfg, job, _runs, transcript, (attention,) = make(
        lambda cfg: FakeTranscriber(), audio_seconds=None
    )
    assert (job.status, job.attempts, transcript) == (JobStatus.FAILED, 1, None)
    assert "audio.wav is missing; re-run the job from Imported" in job.last_error
    assert attention.kind == "failed"


def test_a_missing_model_fails_at_once_with_the_fix(make):
    _cfg, job, *_ = make(make_transcriber, model="no-such-model")
    assert job.status is JobStatus.FAILED and job.attempts == 1
    assert "speech model 'no-such-model' is not installed" in job.last_error
    assert "setup_whisper.py --model no-such-model" in job.last_error


LESSON = [Segment(0, 4, "Խոսքը դիկշների մասին է"), Segment(4, 9, "Python-ում ամեն ինչ ոբեկտ է")]


def test_english_terms_are_put_back_in_latin_letters(make):
    _cfg, job, (run,), transcript, _ = make(lambda cfg: FakeTranscriber(LESSON))
    assert [s.text for s in transcript.segments] == [
        "Խոսքը dictionary-ների մասին է", "Python-ում ամեն ինչ object է",
    ]  # fmt: skip
    assert transcript.details["term_fixes"] == {
        "դիկշների -> dictionary-ների": 1, "ոբեկտ -> object": 1,
    }  # fmt: skip
    assert run.metrics["term_fixes"] == 2
    off = "fix_terms = false"
    _cfg, _job, _runs, kept, _ = make(lambda cfg: FakeTranscriber(LESSON), stt_extra=off)
    assert [s.text for s in kept.segments] == [s.text for s in LESSON]
    assert kept.details["term_fixes"] == {}


def test_more_spellings_come_from_the_config(make):
    extra = "\n".join(["[stt.terms]", '"call stack" = ["կոլ ստեկ"]', "object = []"])
    segments = [*LESSON, Segment(9, 12, "կոլ ստեկը լցվեց")]
    _cfg, _job, _runs, transcript, _ = make(lambda cfg: FakeTranscriber(segments), stt_extra=extra)
    assert [s.text for s in transcript.segments][1:] == [
        "Python-ում ամեն ինչ ոբեկտ է", "call stack-ը լցվեց",  # object was dropped
    ]  # fmt: skip


def test_an_empty_transcript_is_flagged(make):
    _cfg, job, _runs, transcript, _ = make(lambda cfg: FakeTranscriber([]))
    assert job.stage is Stage.CLEAN and job.flags == ["empty_transcript"]
    assert transcript.segments == ()


def test_repeated_segments_are_dropped():
    texts = ["Բարև", "Շնորհակալություն", "շնորհակալություն ", "Շնորհակալություն", "", "Վերջ"]
    segments = [Segment(i, i + 1, text) for i, text in enumerate(texts)]
    kept, dropped = _drop_repeats(segments)
    assert [s.text for s in kept] == ["Բարև", "Շնորհակալություն", "շնորհակալություն ", "Վերջ"]
    assert dropped == 2
