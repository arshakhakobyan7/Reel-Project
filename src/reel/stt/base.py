"""Speech-to-text interface (T-301) and a scripted fake for tests.

The real engine (faster-whisper, T-302) and ``FakeTranscriber`` have the same interface, so
every later stage can be tested without a speech model.
"""

import wave
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from reel.stt.transcript import Segment, TranscriptError, Transcription, Word

Progress = Callable[[float], None]  # seconds of audio done


class Transcriber(ABC):
    name: str

    @abstractmethod
    def transcribe(
        self,
        audio: Path,
        *,
        language: str,
        prompt: str = "",
        on_progress: Progress | None = None,
        check: Callable[[], None] | None = None,
    ) -> Transcription:
        """Transcribe a WAV file. ``prompt`` holds words that help recognition (the group's
        vocabulary). ``check()`` is called often and raises when the work must stop."""


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / audio.getframerate()
    except (OSError, EOFError, wave.Error) as exc:
        raise TranscriptError(f"cannot read the WAV file {path}: {exc}") from exc


# A scripted Armenian lesson: (start, end as a fraction of the duration, text). Chatter before
# class, greeting and topic, a break, the closing and chatter after class. The phrases are
# the default boundary and break phrases (config [cleaning.boundaries]).
LESSON_SCRIPT: tuple[tuple[float, float, str], ...] = (
    (0.01, 0.04, "Լսու՞մ եք ինձ, միկրոֆոնը աշխատում է։"),
    (0.05, 0.08, "Մի րոպե, սպասենք մյուսներին։"),
    (0.10, 0.11, "Բարև ձեզ, սկսում ենք։"),
    (0.12, 0.14, "Այսօր կխոսենք ցուցակների մասին։"),
    (0.16, 0.24, "Ցուցակը ստեղծում ենք քառակուսի փակագծերով, օրինակ list = [1, 2, 3]։"),
    (0.26, 0.34, "Հիմա գրենք for ցիկլ և տպենք յուրաքանչյուր տարրը print ֆունկցիայով։"),
    (0.36, 0.44, "append մեթոդը ավելացնում է նոր տարր ցուցակի վերջում։"),
    (0.45, 0.46, "Հիմա տասը րոպե ընդմիջում։"),
    (0.60, 0.61, "Շարունակում ենք։"),
    (0.62, 0.70, "Հիմա նայենք dictionary-ին. այն պահում է բանալի և արժեք զույգեր։"),
    (0.72, 0.80, "keys և values մեթոդները վերադարձնում են բանալիներն ու արժեքները։"),
    (0.82, 0.89, "Տնային աշխատանքը կգտնեք Classroom-ում։"),
    (0.90, 0.92, "Այսօրվա համար այսքանը, շնորհակալություն ուշադրության համար։"),
    (0.95, 0.98, "Ով հարց ունի, թող մնա։"),
)
# Where the scripted lesson's events are (fractions of the duration), for later stage tests.
LESSON_MARKS = {
    "greeting": 0.10,
    "topic": 0.12,
    "break_start": 0.45,
    "break_resume": 0.60,
    "closing_end": 0.92,
}


def _spread_words(start: float, end: float, text: str) -> tuple[Word, ...]:
    tokens = text.split()
    step = (end - start) / len(tokens)
    return tuple(
        Word(round(start + i * step, 3), round(start + (i + 1) * step, 3), token, 0.9)
        for i, token in enumerate(tokens)
    )


def scripted_lesson(duration: float) -> tuple[Segment, ...]:
    """``LESSON_SCRIPT`` stretched over ``duration`` seconds, with word timings."""
    segments = []
    for begin, finish, text in LESSON_SCRIPT:
        start, end = round(begin * duration, 3), round(finish * duration, 3)
        segments.append(Segment(start, end, text, _spread_words(start, end, text), -0.2, 0.01))
    return tuple(segments)


class FakeTranscriber(Transcriber):
    """Returns scripted segments: the given ones, or ``scripted_lesson()`` fitted to the
    length of the audio file. Reports progress per segment and honours ``check()``."""

    def __init__(self, segments: Iterable[Segment] | None = None, *, name: str = "fake") -> None:
        self.name = name
        self.segments = None if segments is None else tuple(segments)
        self.calls: list[dict[str, Any]] = []

    def transcribe(
        self,
        audio: Path,
        *,
        language: str,
        prompt: str = "",
        on_progress: Progress | None = None,
        check: Callable[[], None] | None = None,
    ) -> Transcription:
        self.calls.append({"audio": Path(audio), "language": language, "prompt": prompt})
        if self.segments is None:
            duration = wav_duration(audio)
            segments = scripted_lesson(duration)
        else:
            segments = self.segments
            duration = max((s.end for s in segments), default=0.0)
        for segment in segments:
            if check:
                check()
            if on_progress:
                on_progress(segment.end)
        if on_progress:
            on_progress(duration)
        return Transcription(language, segments, self.name, {"prompt": prompt})
