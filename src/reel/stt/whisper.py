"""Local speech recognition with faster-whisper (T-302; FR-14, R-4, D-002).

faster-whisper (MIT) runs Whisper models with CTranslate2 on the CPU (int8 by default). The
model files live in ``<models_dir>/whisper/<model>`` (``scripts/setup_whisper.py`` downloads
them once); at run time nothing is downloaded. Loaded models are kept for the next job.

Settings that reduce invented text (R-4): Silero VAD skips non-speech, the previous window's
text is not fed back (``condition_on_previous_text=False``, which stops repetition loops),
long silences inside a window are skipped (``hallucination_silence_threshold``), and runs of
the same segment text repeated more than twice are dropped.
"""

import logging
import os
import threading
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from reel.config.loader import LoadedConfig
from reel.config.schema import SttSettings
from reel.stt.base import Progress, Transcriber
from reel.stt.transcript import Segment, TranscriptError, Transcription, Word

log = logging.getLogger("reel.stt")
SAMPLE_RATE = 16000
MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")
MAX_REPEATS = 2
# Text that compresses this well is a repetition loop ("աստ, աստ, աստ, ..."); Whisper's own
# threshold. Without temperature fallback such windows are kept by Whisper, so Reel drops them.
MAX_COMPRESSION_RATIO = 2.4
_models: dict[tuple[str, str, int], Any] = {}
_models_lock = threading.Lock()


class ModelMissing(RuntimeError):
    """The configured speech model is not installed."""


def model_dir(models_dir: Path, model: str) -> Path:
    return Path(models_dir) / "whisper" / model


def model_installed(path: Path) -> bool:
    return all((path / name).is_file() for name in MODEL_FILES)


def cpu_threads(settings: SttSettings) -> int:
    """``stt.cpu_threads``; 0 = all logical CPUs but 2, so the PC stays usable."""
    return settings.cpu_threads or max(1, (os.cpu_count() or 4) - 2)


def _load(path: Path, compute_type: str, cpu_threads: int) -> Any:
    key = (str(path), compute_type, cpu_threads)
    with _models_lock:
        if key not in _models:
            from faster_whisper import WhisperModel  # heavy import, only when needed

            log.info("loading speech model %s (%s)", path.name, compute_type)
            _models[key] = WhisperModel(
                str(path), device="cpu", compute_type=compute_type, cpu_threads=cpu_threads
            )
        return _models[key]


def load_wav(path: Path) -> Any:
    """The import stage's 16 kHz mono 16-bit WAV as float32 samples for Whisper.

    Read here rather than by faster-whisper (PyAV), which keeps Reel independent of PyAV
    version changes (PyAV 19 broke faster-whisper 1.2.1's own decoder)."""
    import numpy as np

    try:
        with wave.open(str(path), "rb") as audio:
            layout = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
            frames = audio.readframes(audio.getnframes())
    except (OSError, EOFError, wave.Error) as exc:
        raise TranscriptError(f"cannot read the WAV file {path}: {exc}") from exc
    if layout != (1, 2, SAMPLE_RATE):
        raise TranscriptError(
            f"{path} must be 16 kHz mono 16-bit WAV (got {layout[2]} Hz, {layout[0]} channel(s), "
            f"{layout[1] * 8}-bit)"
        )
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


def _drop_repeats(segments: list[Segment]) -> tuple[list[Segment], int]:
    kept: list[Segment] = []
    dropped, run, previous = 0, 0, None
    for segment in segments:
        key = " ".join(segment.text.split()).casefold()
        if not key:
            dropped += 1
            continue
        run = run + 1 if key == previous else 1
        previous = key
        if run > MAX_REPEATS:
            dropped += 1
            continue
        kept.append(segment)
    return kept, dropped


class WhisperTranscriber(Transcriber):
    def __init__(self, settings: SttSettings, models_dir: Path) -> None:
        self.settings = settings
        self.path = model_dir(models_dir, settings.model)
        self.name = f"faster-whisper {settings.model} {settings.compute_type}"
        self._lock = threading.Lock()  # one transcription per model at a time
        if not model_installed(self.path):
            raise ModelMissing(
                f"speech model {settings.model!r} is not installed in {self.path}; run "
                f"scripts/setup_whisper.py --model {settings.model}"
            )

    def transcribe(
        self,
        audio: Path,
        *,
        language: str,
        prompt: str = "",
        on_progress: Progress | None = None,
        check: Callable[[], None] | None = None,
    ) -> Transcription:
        stt = self.settings
        model = _load(self.path, stt.compute_type, cpu_threads(stt))
        options: dict[str, Any] = {
            "language": language,
            "task": "transcribe",
            "beam_size": stt.beam_size,
            "initial_prompt": prompt or None,
            "condition_on_previous_text": False,
            "vad_filter": stt.vad,
            "word_timestamps": stt.word_timestamps,
        }
        if not stt.temperature_fallback:
            options["temperature"] = [0.0]
        if stt.word_timestamps:
            options["hallucination_silence_threshold"] = 2.0
        with self._lock:
            pieces, info = model.transcribe(load_wav(audio), **options)
            segments, looping = [], 0
            for piece in pieces:  # decoding happens while iterating
                if on_progress:
                    on_progress(float(piece.end))
                if check:
                    check()
                if piece.compression_ratio > MAX_COMPRESSION_RATIO:
                    looping += 1
                    continue
                words = tuple(
                    Word(
                        max(float(w.start), 0.0),
                        max(float(w.end), float(w.start), 0.0),
                        w.word.strip(),
                        float(w.probability),
                    )
                    for w in (piece.words or ())
                )
                start = max(float(piece.start), 0.0)
                segments.append(
                    Segment(
                        start,
                        max(float(piece.end), start),
                        piece.text.strip(),
                        words,
                        float(piece.avg_logprob),
                        float(piece.no_speech_prob),
                    )
                )
        segments.sort(key=lambda segment: segment.start)
        kept, dropped = _drop_repeats(segments)
        if on_progress:
            on_progress(float(info.duration))
        details = {
            "prompt": prompt,
            "language_probability": round(float(info.language_probability), 4),
            "audio_seconds": round(float(info.duration), 3),
            "speech_seconds": round(float(info.duration_after_vad), 3),
            "dropped_segments": dropped + looping,
            "dropped_loops": looping,
            "options": {k: v for k, v in options.items() if k != "initial_prompt"},
        }
        return Transcription(language, tuple(kept), self.name, details)


def make_transcriber(cfg: LoadedConfig) -> Transcriber:
    """The transcriber chosen in ``[stt]`` (only faster-whisper so far)."""
    return WhisperTranscriber(cfg.settings.stt, cfg.paths.models_dir)
