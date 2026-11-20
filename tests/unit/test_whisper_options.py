"""WhisperTranscriber options and filters with a stand-in model (T-302/T-303); no real model."""

import os
import wave
from types import SimpleNamespace

import pytest

from reel.config.schema import SttSettings
from reel.stt import whisper
from reel.stt.whisper import MODEL_FILES, WhisperTranscriber, cpu_threads


def piece(start, end, text, ratio=1.2):
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        words=[SimpleNamespace(start=start, end=end, word=f" {text}", probability=0.9)],
        avg_logprob=-0.3,
        no_speech_prob=0.01,
        compression_ratio=ratio,
    )


class StandInModel:
    def __init__(self, pieces):
        self.pieces = pieces
        self.options = None

    def transcribe(self, audio, **options):
        self.options = options
        info = SimpleNamespace(duration=10.0, duration_after_vad=8.0, language_probability=0.97)
        return iter(self.pieces), info


@pytest.fixture
def setup(tmp_path, monkeypatch):
    model_path = tmp_path / "models" / "whisper" / "large-v3-turbo"
    model_path.mkdir(parents=True)
    for name in MODEL_FILES:
        (model_path / name).write_text("x")
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(16000)
        file.writeframes(b"\0\0" * 16000)

    def make(pieces, **settings):
        model = StandInModel(pieces)
        monkeypatch.setattr(whisper, "_load", lambda *args: model)
        stt = SttSettings(model="large-v3-turbo", **settings)
        return WhisperTranscriber(stt, tmp_path / "models"), model, audio

    return make


def test_repetition_loops_are_dropped_and_progress_continues(setup):
    loop = "աստ, " * 40
    transcriber, _model, audio = setup(
        [piece(0, 2, "Բարև ձեզ"), piece(2, 6, loop, ratio=9.5), piece(6, 9, "Շարունակենք")]
    )
    progress = []
    result = transcriber.transcribe(audio, language="hy", on_progress=progress.append)
    assert [s.text for s in result.segments] == ["Բարև ձեզ", "Շարունակենք"]
    assert result.details["dropped_loops"] == 1 and result.details["dropped_segments"] == 1
    assert progress == [2.0, 6.0, 9.0, 10.0]
    assert all(type(value) is float for value in progress)


@pytest.mark.parametrize(("fallback", "expected"), [(False, [0.0]), (True, None)])
def test_temperature_fallback_setting(setup, fallback, expected):
    transcriber, model, audio = setup([], temperature_fallback=fallback, beam_size=1)
    transcriber.transcribe(audio, language="hy", prompt="Python, list")
    assert model.options.get("temperature") == expected
    assert model.options["beam_size"] == 1 and model.options["initial_prompt"] == "Python, list"
    assert model.options["condition_on_previous_text"] is False


def test_automatic_cpu_threads_leave_two_free():
    assert cpu_threads(SttSettings(cpu_threads=3)) == 3
    assert cpu_threads(SttSettings(cpu_threads=0)) == max(1, (os.cpu_count() or 4) - 2)
