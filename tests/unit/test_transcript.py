"""Transcript format, storage and the fake transcriber (T-301)."""

import json
import math
import wave

import pytest

from reel.db import Recording, TranscriptKind
from reel.queue.jobs import create_job
from reel.stt import (
    LESSON_MARKS,
    FakeTranscriber,
    Segment,
    Transcriber,
    TranscriptError,
    Transcription,
    Word,
    load_transcript,
    read_json,
    save_transcript,
    words,
    write_json,
)

SAMPLE = Transcription(
    "hy",
    (
        Segment(
            1.0, 2.5, "Բարև ձեզ։", (Word(1.0, 1.6, "Բարև", 0.91), Word(1.6, 2.5, "ձեզ։")), -0.3
        ),
        Segment(3.0, 4.25, "print ֆունկցիան", no_speech_prob=0.02),
        Segment(3.5, 5.0, ""),  # overlaps the previous one: allowed, only the starts are ordered
    ),
    "test-model",
    {"prompt": "Python, list"},
)


def test_round_trip_through_dict_and_json():
    assert Transcription.from_dict(SAMPLE.to_dict()) == SAMPLE
    text = SAMPLE.to_json()
    assert "Բարև ձեզ" in text  # stored as readable UTF-8, not \u escapes
    assert Transcription.from_json(text) == SAMPLE
    assert json.loads(text)["segments"][0]["words"][1] == [1.6, 2.5, "ձեզ։"]


def test_times_are_kept_to_the_millisecond():
    data = Transcription("hy", (Segment(1.23456, 2.0004, "x"),)).to_dict()
    assert (data["segments"][0]["start"], data["segments"][0]["end"]) == (1.235, 2.0)


def test_text_end_and_between():
    assert SAMPLE.text == "Բարև ձեզ։ print ֆունկցիան"
    assert SAMPLE.end == 5.0
    assert [s.text for s in SAMPLE.between(2.0, 3.2)] == ["Բարև ձեզ։", "print ֆունկցիան"]
    assert SAMPLE.between(10, 20) == ()
    assert [w.text for w in words(SAMPLE.segments)] == ["Բարև", "ձեզ։", "print ֆունկցիան", ""]


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: Segment(-1, 2, "x"), "must not be negative"),
        (lambda: Segment(3, 2, "x"), "ends"),
        (lambda: Segment(math.nan, 2, "x"), "finite"),
        (lambda: Segment(0, math.inf, "x"), "finite"),
        (lambda: Segment(0, 1, "x", avg_logprob=math.nan), "avg_logprob"),
        (lambda: Word(2, 1, "x"), "ends"),
        (lambda: Segment(True, 1, "x"), "finite number"),
        (lambda: Transcription("hy", (Segment(5, 6, "b"), Segment(1, 2, "a"))), "time order"),
    ],
)
def test_invalid_values_are_rejected(build, message):
    with pytest.raises(TranscriptError, match=message):
        build()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"schema": 2, "language": "hy", "segments": []}, "schema"),
        ({"schema": 1, "segments": []}, "language"),
        ({"schema": 1, "language": "hy", "segments": [{"start": 0, "text": "x"}]}, "'end'"),
        ({"schema": 1, "language": "hy", "segments": ["x"]}, "must be an object"),
        (
            {
                "schema": 1,
                "language": "hy",
                "segments": [{"start": 0, "end": 1, "text": "x", "words": [[0, 1, "a", 0.5, 9]]}],
            },
            "invalid word",
        ),
        ([], "JSON object"),
    ],
)
def test_invalid_data_is_rejected(data, message):
    with pytest.raises(TranscriptError, match=message):
        Transcription.from_dict(data)
    with pytest.raises(TranscriptError, match="not valid JSON"):
        Transcription.from_json("{")


def test_json_files(tmp_path):
    path = write_json(tmp_path / "jobs" / "1" / "transcript.raw.json", SAMPLE)
    assert read_json(path) == SAMPLE
    assert not list(path.parent.glob("*.part"))
    assert "Բարև" in path.read_text(encoding="utf-8")


def test_database_storage_raw_and_clean(db):
    with db.transaction() as s:
        recording = Recording(source_path="x.mp4", size_bytes=1)
        s.add(recording)
        job_id = create_job(s, recording).id
        save_transcript(s, job_id, TranscriptKind.RAW, SAMPLE)
    clean = Transcription("hy", SAMPLE.segments[:1], "test-model")
    with db.transaction() as s:
        save_transcript(s, job_id, TranscriptKind.CLEAN, clean)
        row = save_transcript(s, job_id, TranscriptKind.RAW, SAMPLE)  # a re-run replaces it
        assert row.text == SAMPLE.text
    with db.transaction() as s:
        assert load_transcript(s, job_id, TranscriptKind.RAW) == SAMPLE
        assert load_transcript(s, job_id, TranscriptKind.CLEAN) == clean
        job = s.get(type(recording.job), job_id)
        assert len(job.transcripts) == 2
        assert load_transcript(s, job_id + 1, TranscriptKind.RAW) is None


def silent_wav(path, seconds: float, rate: int = 1000):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * int(seconds * rate))
    return path


def test_the_fake_transcriber_scripts_a_lesson_to_the_audio_length(tmp_path):
    audio = silent_wav(tmp_path / "audio.wav", 600)
    progress, checks = [], []
    fake = FakeTranscriber()
    result = fake.transcribe(
        audio,
        language="hy",
        prompt="Python, list",
        on_progress=progress.append,
        check=lambda: checks.append(1),
    )
    assert isinstance(fake, Transcriber) and result.model == "fake"
    assert result.language == "hy" and result.details == {"prompt": "Python, list"}
    assert result.segments[0].start > 0 and result.end <= 600
    assert progress == sorted(progress) and progress[-1] == 600
    assert len(checks) == len(result.segments)
    by_start = {round(s.start / 600, 2): s.text for s in result.segments}
    assert "Բարև ձեզ" in by_start[LESSON_MARKS["greeting"]]
    assert "ընդմիջում" in by_start[LESSON_MARKS["break_start"]]
    assert "Շարունակում ենք" in by_start[LESSON_MARKS["break_resume"]]
    assert all(seg.words and seg.words[-1].end == seg.end for seg in result.segments)
    assert fake.calls == [{"audio": audio, "language": "hy", "prompt": "Python, list"}]


def test_the_fake_transcriber_returns_given_segments_and_can_be_stopped(tmp_path):
    fake = FakeTranscriber(SAMPLE.segments, name="scripted")
    assert fake.transcribe(tmp_path / "none.wav", language="hy").segments == SAMPLE.segments

    def stop():
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        fake.transcribe(tmp_path / "none.wav", language="hy", check=stop)
    with pytest.raises(TranscriptError, match="cannot read the WAV file"):
        FakeTranscriber().transcribe(tmp_path / "none.wav", language="hy")
