"""Smoke tests with the real faster-whisper ``tiny`` model (T-302).

Install it first: ``.venv\\Scripts\\python scripts\\setup_whisper.py --model tiny``.
"""

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from reel.config import load_config
from reel.db import Job, JobStatus, Stage, StageRun, TranscriptKind, init_database, open_database
from reel.media.ffmpeg import extract_audio
from reel.media.testmedia import make_lesson
from reel.queue import StageRegistry, Worker
from reel.stages.importing import ImportStage
from reel.stages.transcribing import TranscribeStage
from reel.stt import load_transcript
from reel.stt.whisper import WhisperTranscriber, model_dir, model_installed

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "ffmpeg" / "bin"
FFMPEG, FFPROBE = BIN / "ffmpeg.exe", BIN / "ffprobe.exe"
MODELS = ROOT / "models"
pytest.importorskip("faster_whisper")
pytestmark = pytest.mark.skipif(
    not (FFMPEG.is_file() and model_installed(model_dir(MODELS, "tiny"))),
    reason="needs FFmpeg and the tiny model (scripts/setup_ffmpeg.py, scripts/setup_whisper.py)",
)

CONFIG = f"""
[paths]
ffmpeg = '{FFMPEG.as_posix()}'
ffprobe = '{FFPROBE.as_posix()}'
models_dir = '{MODELS.as_posix()}'

[ingest]
min_duration_minutes = 0.1

[stt]
model = "tiny"
beam_size = 1

[workers]
max_attempts = 1
idle_poll_seconds = 0.02
"""


def test_import_and_transcribe_end_to_end(project, write_config):
    write_config(CONFIG)
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    try:
        source = make_lesson(FFMPEG, project / "inbox" / "Python 12" / "Lecture 1.mp4")
        from reel.db import Recording
        from reel.queue import create_job

        with db.transaction() as s:
            recording = Recording(source_path=str(source), size_bytes=source.stat().st_size)
            s.add(recording)
            job_id = create_job(s, recording).id
        registry = StageRegistry([ImportStage(), TranscribeStage()])  # up to Transcribed
        worker = Worker(db, registry, lambda: cfg, heartbeat_seconds=0.05)
        worker.start(until_idle=True)
        assert worker.join(300)
        with db.transaction() as s:
            job = s.get(Job, job_id)
            assert (job.stage, job.status) == (Stage.CLEAN, JobStatus.QUEUED), job.last_error
            transcript = load_transcript(s, job_id, TranscriptKind.RAW)
            run = s.scalars(
                select(StageRun).where(
                    StageRun.job_id == job_id, StageRun.stage == Stage.TRANSCRIBE
                )
            ).one()
            assert run.metrics["model"] == "faster-whisper tiny int8"
            assert run.metrics["audio_seconds"] == pytest.approx(10, abs=0.2)
        assert transcript.language == "hy"
        assert transcript.details["audio_seconds"] == pytest.approx(10, abs=0.2)
        assert (cfg.paths.data_dir / "jobs" / str(job_id) / "transcript.raw.json").is_file()
    finally:
        db.dispose()


def _speak(path: Path, text: str) -> bool:
    """English speech from the Windows speech synthesiser (part of Windows, free)."""
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{text}'); $s.Dispose()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            timeout=60,
        )
    except OSError, subprocess.SubprocessError:
        return False
    return path.is_file() and path.stat().st_size > 1000


def test_the_model_really_recognises_speech(tmp_path, project):
    spoken = tmp_path / "speech.wav"
    if not _speak(spoken, "Hello everyone. Today we will talk about lists in Python."):
        pytest.skip("the Windows speech synthesiser is not available")
    audio = extract_audio(FFMPEG, spoken, tmp_path / "audio.wav")  # as the import stage does
    cfg = load_config(project / "config" / "reel.toml")
    settings = cfg.settings.stt.model_copy(
        update={"model": "tiny", "beam_size": 1, "word_timestamps": True}
    )
    progress = []
    result = WhisperTranscriber(settings, MODELS).transcribe(
        audio, language="en", on_progress=progress.append
    )
    text = result.text.lower()
    assert "python" in text and "today" in text, text
    assert all(segment.words for segment in result.segments)  # word timestamps are on
    assert progress == sorted(progress) and all(type(p) is float for p in progress)
    assert progress[-1] == pytest.approx(result.details["audio_seconds"], abs=0.001)
