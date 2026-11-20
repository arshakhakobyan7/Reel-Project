"""Lesson start and end on the raw timeline (T-402; FR-7, D-005, R-1).

The lecturer's own words mark the lesson. In the first ``start_search_minutes`` Reel looks
for greetings ("բարև ձեզ", ...) and topic openers ("այսօր կխոսենք", ...); in the last
``end_search_minutes`` for closings ("այսօրվա համար այսքանը", ...). Matching is Armenian-aware
(letter case, punctuation inside words, "և"/"եւ") and fuzzy, because the transcript has
recognition errors.

- Start: the first topic opener with a greeting at most ``anchor_window_minutes`` before it
  starts the lesson at that greeting (a student's "բարև" during the chatter has no topic
  opener after it). Otherwise the LLM (``use_llm``) is asked for the line where the lecturer
  greets or announces the start; teaching and recaps are not such a line.
  A topic opener alone is not enough: in the real sample lesson the recording starts with a
  20-minute recap and "այսօր անցնելու ենք ..." comes only after it (T-402).
- End: the last closing phrase ends the lesson at the end of its segment; otherwise the LLM.
- The LLM only confirms, it never decides alone (D-059, the user's rule): the line it names
  must hold one of the configured phrases. A recording often starts right when the teaching
  starts, with no greeting at all; on such a recording the small local model once named an
  ordinary teaching line ("now let's take an example") as a greeting, and 2:48 of the lesson
  were cut. A greeting in other words is added to the phrase lists instead.
- Without confident evidence nothing is cut and the job gets a flag.
"""

import logging
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal

from pydantic import BaseModel

from reel.config.prompts import PromptLibrary
from reel.config.schema import BoundarySettings
from reel.llm import LLMClient
from reel.stt import Segment, Transcription

log = logging.getLogger("reel.cleaning")
START_NOT_FOUND = "lesson_start_not_found"
END_NOT_FOUND = "lesson_end_not_found"
CONFLICT = "lesson_boundaries_conflict"
LLM_TEXT_LIMIT = 8000  # characters of transcript given to the LLM (fits its context)

_INSIDE_WORD = dict.fromkeys(map(ord, "՛՜՞­"), None)  # ՛ ՜ ՞, soft hyphen
_PUNCTUATION = re.compile(r"[^\w\s]|_")

Kind = Literal["greeting", "topic", "closing"]

# Letters speech recognition mixes up in Armenian (seen in the real lesson: "մեթոտ" for "մեթոդ",
# "կործնական" for "գործնական", "վունկցիա" for "ֆունկցիա"). Folded only for matching.
_SOUNDS = str.maketrans(
    {
        "օ": "ո",
        "է": "ե",
        "ֆ": "վ",
        "ռ": "ր",
        "բ": "պ",
        "փ": "պ",
        "գ": "կ",
        "ք": "կ",
        "դ": "տ",
        "թ": "տ",
        "ձ": "ծ",
        "ց": "ծ",
        "ջ": "ճ",
        "չ": "ճ",
    }
)


def normalize(text: str) -> str:
    """Lower case, "և" written as "եւ", no punctuation (marks inside words are dropped)."""
    text = unicodedata.normalize("NFC", text).casefold().translate(_INSIDE_WORD)
    text = text.replace("և", "եւ")  # և → եւ
    return " ".join(_PUNCTUATION.sub(" ", text).split())


def phonetic(text: str) -> str:
    """``normalize`` plus folding of the letters STT confuses ("եւ" counts as "եվ")."""
    return normalize(text).replace("եւ", "եվ").translate(_SOUNDS)


@dataclass(frozen=True)
class PhraseMatch:
    kind: Kind
    phrase: str
    found: str  # the words that matched
    score: float
    segment: int  # index in the transcript
    start: float
    end: float


@dataclass(frozen=True)
class Boundary:
    seconds: float
    method: str  # "greeting + topic", "topic", "closing", "llm"
    evidence: tuple[PhraseMatch, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class LessonBoundaries:
    start: Boundary | None
    end: Boundary | None
    flags: tuple[str, ...] = ()
    llm_calls: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def similarity(a: str, b: str) -> float:
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    if matcher.real_quick_ratio() < 0.5 or matcher.quick_ratio() < 0.5:
        return 0.0
    return matcher.ratio()


def find_phrases(
    segments: Sequence[Segment],
    phrases: Iterable[str],
    kind: Kind,
    threshold: float,
    *,
    indices: Iterable[int] | None = None,
) -> list[PhraseMatch]:
    """The best match of each segment for any of the phrases (score ≥ ``threshold``)."""
    targets = [(phrase, phonetic(phrase)) for phrase in phrases]
    found = []
    for index in indices if indices is not None else range(len(segments)):
        segment = segments[index]
        words = phonetic(segment.text).split()
        best: PhraseMatch | None = None
        for phrase, target in targets:
            size = len(target.split())
            for width in {max(1, size - 1), size, size + 1}:
                for first in range(max(1, len(words) - width + 1)):
                    window = " ".join(words[first : first + width])
                    score = similarity(window, target)
                    if score >= threshold and (best is None or score > best.score):
                        best = PhraseMatch(
                            kind, phrase, window, round(score, 3), index, segment.start, segment.end
                        )
        if best:
            found.append(best)
    return found


class _Answer(BaseModel):
    found: bool
    segment: int | None = None
    quote: str = ""  # the line's own words that greet / announce; Reel checks they are there
    evidence: str = ""


QUOTE_THRESHOLD = 0.8


def _llm_judge(
    llm: LLMClient,
    prompts: PromptLibrary,
    prompt_name: str,
    segments: Sequence[Segment],
    indices: list[int],
    phrases: Iterable[str],
    check: Callable[[], None] | None,
    *,
    kind: Kind,
    threshold: float,
    keep_last: bool = False,
) -> tuple[int | None, str]:
    """``keep_last``: when the window has more text than the LLM gets, keep its last lines (the
    end of a lesson) instead of its first ones. The named line must hold one of ``phrases``
    (score ≥ ``threshold``), or the answer is rejected (D-059)."""
    phrases = tuple(phrases)
    lines, size = [], 0
    for index in reversed(indices) if keep_last else indices:
        text = segments[index].text.strip()
        if not text:
            continue
        line = f"[{index}] {_clock(segments[index].start)} {text}"
        if size + len(line) > LLM_TEXT_LIMIT:
            break
        lines.append(line)
        size += len(line) + 1
    if not lines:
        return None, "nothing was said in the search window"
    if keep_last:
        lines.reverse()
    prompt = prompts.render(prompt_name, segments="\n".join(lines), phrases=", ".join(phrases))
    answer = llm.generate(prompt, _Answer, check=check).value
    if not answer.found or answer.segment not in indices:
        return None, answer.evidence.strip() or "the LLM found no clear boundary"
    # A small local model sometimes names a line and invents what it says (seen on the real
    # lesson). Its quote must really be in that line, allowing for recognition errors.
    quote = answer.quote.strip()
    if not quote or not find_phrases(
        segments, [quote], "greeting", QUOTE_THRESHOLD, indices=[answer.segment]
    ):
        log.info("LLM answer rejected: quote %r is not in line %s", quote, answer.segment)
        return None, f"rejected: the quoted words are not in line {answer.segment}"
    if not find_phrases(segments, phrases, kind, threshold, indices=[answer.segment]):
        log.info("LLM answer rejected: line %s holds none of the phrases", answer.segment)
        return None, f"rejected: line {answer.segment} holds none of the configured phrases"
    return answer.segment, f"«{quote}» — {answer.evidence.strip()}".strip(" —")


def _clock(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes // 60}:{minutes % 60:02d}:{rest:02d}"


def find_start(
    transcript: Transcription,
    settings: BoundarySettings,
    *,
    llm: LLMClient | None = None,
    prompts: PromptLibrary | None = None,
    check: Callable[[], None] | None = None,
) -> tuple[Boundary | None, int]:
    """The lesson start (or None) and the number of LLM calls made."""
    segments = transcript.segments
    limit = settings.start_search_minutes * 60
    window = [i for i, s in enumerate(segments) if s.start < limit]
    threshold = settings.fuzzy_threshold
    greetings = find_phrases(
        segments, settings.greeting_phrases, "greeting", threshold, indices=window
    )
    topics = find_phrases(segments, settings.topic_phrases, "topic", threshold, indices=window)
    anchor = settings.anchor_window_minutes * 60
    for topic in topics:  # earliest topic opener first
        before = [g for g in greetings if topic.start - anchor <= g.start <= topic.start]
        if before:
            greeting = before[-1]  # the lecturer's greeting right before the topic
            return Boundary(greeting.start, "greeting + topic", (greeting, topic)), 0
    if settings.use_llm and llm is not None and prompts is not None:
        phrases = (*settings.greeting_phrases, *settings.topic_phrases)
        index, evidence = _llm_judge(
            llm, prompts, "lesson_start", segments, window, phrases, check,
            kind="greeting", threshold=threshold,
        )  # fmt: skip
        if index is not None:
            return Boundary(segments[index].start, "llm", (*greetings, *topics), evidence), 1
        return None, 1
    return None, 0


def find_end(
    transcript: Transcription,
    settings: BoundarySettings,
    *,
    duration: float | None = None,
    llm: LLMClient | None = None,
    prompts: PromptLibrary | None = None,
    check: Callable[[], None] | None = None,
) -> tuple[Boundary | None, int]:
    """The lesson end (or None) and the number of LLM calls made."""
    segments = transcript.segments
    end = duration if duration is not None else transcript.end
    limit = end - settings.end_search_minutes * 60
    window = [i for i, s in enumerate(segments) if s.end > limit]
    closings = find_phrases(
        segments, settings.closing_phrases, "closing", settings.fuzzy_threshold, indices=window
    )
    if closings:
        last = closings[-1]
        return Boundary(last.end, "closing", (last,)), 0
    if settings.use_llm and llm is not None and prompts is not None:
        index, evidence = _llm_judge(
            llm,
            prompts,
            "lesson_end",
            segments,
            window,
            settings.closing_phrases,
            check,
            kind="closing",
            threshold=settings.fuzzy_threshold,
            keep_last=True,
        )
        if index is not None:
            return Boundary(segments[index].end, "llm", (), evidence), 1
        return None, 1
    return None, 0


def detect_boundaries(
    transcript: Transcription,
    settings: BoundarySettings,
    *,
    duration: float | None = None,
    llm: LLMClient | None = None,
    prompts: PromptLibrary | None = None,
    check: Callable[[], None] | None = None,
) -> LessonBoundaries:
    """Start and end of the lesson; a missing one is not cut and adds a flag. LLM errors are
    raised (``LLMError``), so the stage can retry instead of silently cutting nothing."""
    start, start_calls = find_start(transcript, settings, llm=llm, prompts=prompts, check=check)
    end, end_calls = find_end(
        transcript, settings, duration=duration, llm=llm, prompts=prompts, check=check
    )
    flags = []
    if start and end and start.seconds >= end.seconds:
        log.warning("lesson start %.1f s is not before its end %.1f s", start.seconds, end.seconds)
        start = end = None
        flags.append(CONFLICT)
    if start is None:
        flags.append(START_NOT_FOUND)
    if end is None:
        flags.append(END_NOT_FOUND)
    return LessonBoundaries(start, end, tuple(dict.fromkeys(flags)), start_calls + end_calls)
