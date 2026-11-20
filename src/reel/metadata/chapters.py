"""Chapters from the clean transcript (T-501; FR-15, FR-19, R-3, D-044).

The local model (gemma3:4b) is small and the transcript has recognition errors, so the model
is used only where it is needed and every answer is checked against the transcript:

1. **Map:** the transcript is cut into windows of ``window_minutes`` (a transition phrase such
   as "այսօր անցնելու ենք …" always opens a new window); for each the LLM gives a short topic
   label and 1–3 words copied from the window. A label is accepted only when at least one copied
   word is really in the window and most of its own words are in the window, its neighbours or
   the course vocabulary; otherwise the window has no label (it joins the topic before it).
2. **Group (no LLM):** neighbouring windows about the same topic are merged (word overlap)
   until there are between ``min_chapters`` and about one chapter per 12 minutes; a window
   opened by a transition phrase is merged into the one before it only as the last resort.
3. **Start times:** a chapter starts at its first segment, which is exactly the transition
   phrase when there is one (seen on the real lesson: «այսօր մենք անցնելու ենք …» at 19:17).
4. **Titles:** the LLM writes one short title per chapter from the labels of its windows; a title
   with words that are not in those labels or the text is replaced by the best label.
5. The list is checked with YouTube's rules (``reel.metadata.text.check_chapters``, T-502).
   If no valid list can be made, the video gets no chapters and the job the flag
   ``chapters_not_generated``.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from reel.cleaning.boundaries import find_phrases, phonetic, similarity
from reel.config.prompts import PromptLibrary
from reel.config.schema import MetadataSettings
from reel.llm import LLMClient, LLMError
from reel.metadata.text import Chapter, ChapterError, check_chapters
from reel.stt import Segment, Transcription

log = logging.getLogger("reel.metadata")

NOT_GENERATED = "chapters_not_generated"
WORD_THRESHOLD = 0.8  # a copied word must match the window's text this well (recognition errors)
SUPPORT_THRESHOLD = 0.75  # a label word counts as supported at this similarity
MIN_SUPPORT = 0.5  # share of a label's words that must be supported
MERGE_SIMILARITY = 0.34  # neighbours at least this similar are one topic
MINUTES_PER_CHAPTER = 12
MAX_CHAPTERS = 12
MIN_WINDOW_CHARS = 200  # shorter windows are joined to the previous one
# Words that say nothing about the topic (normalised); short words are ignored anyway.
STOPWORDS = frozenset(
    phonetic(w)
    for w in [
        "այս",
        "այդ",
        "այն",
        "մեր",
        "մենք",
        "դուք",
        "նրանք",
        "հետ",
        "համար",
        "մասին",
        "որը",
        "որոնք",
        "որպես",
        "նաև",
        "կամ",
        "բայց",
        "եթե",
        "երբ",
        "ինչ",
        "ինչպես",
        "հիմա",
        "այսօր",
        "դասի",
        "դասը",
        "դաս",
        "հատված",
        "բաժին",
        "մաս",
        "բացատրություն",
        "աշխատանք",
        "օրինակ",
        "օրինակներ",
        "the",
        "and",
        "with",
        "for",
    ]
)


class _WindowAnswer(BaseModel):
    label: str
    words: list[str] = Field(default_factory=list)


class _TitleAnswer(BaseModel):
    title: str


@dataclass(frozen=True)
class Draft:
    """A chapter before YouTube's rules are applied."""

    start: float
    title: str


@dataclass
class WindowTopic:
    index: int
    first: int  # first and last segment index
    last: int
    start: float
    end: float
    label: str = ""
    words: list[str] = field(default_factory=list)  # copied words that are really in the text
    source: str = ""  # "llm", "" (no usable label), "error"
    opens: str = ""  # the transition phrase that opens this window


@dataclass
class ChapterResult:
    chapters: list[Chapter]
    flags: list[str]
    llm_calls: int
    details: dict[str, Any]


def _tokens(text: str) -> list[str]:
    return [w for w in phonetic(text).split() if len(w) >= 3 and w not in STOPWORDS]


def _stems(text: str) -> set[str]:
    return {w[:5] for w in _tokens(text)}


def support(text: str, reference: Sequence[str]) -> float:
    """Share of the words of ``text`` that appear (fuzzily) in ``reference`` words."""
    words = _tokens(text)
    if not words:
        return 0.0
    known = set(reference)
    hits = 0
    for word in words:
        close = (ref for ref in known if abs(len(ref) - len(word)) <= 4)
        if word in known or any(similarity(word, ref) >= SUPPORT_THRESHOLD for ref in close):
            hits += 1
    return hits / len(words)


def make_windows(
    segments: Sequence[Segment],
    seconds: float,
    phrases: Sequence[str] = (),
    threshold: float = 0.85,
) -> list[WindowTopic]:
    """Consecutive segments in windows of about ``seconds``; a transition phrase opens a new
    window (after the first minute); tiny windows join the previous one."""
    opening = {
        m.segment: m.phrase
        for m in find_phrases(segments, phrases, "topic", threshold)
        if m.start >= 60
    }
    windows: list[WindowTopic] = []
    for index, segment in enumerate(segments):
        if not segment.text.strip():
            continue
        current = windows[-1] if windows else None
        if current is None or segment.start >= current.start + seconds or index in opening:
            window = WindowTopic(len(windows), index, index, segment.start, segment.end)
            window.opens = opening.get(index, "")
            windows.append(window)
        else:
            current.last, current.end = index, max(current.end, segment.end)
    merged: list[WindowTopic] = []
    for window in windows:
        chars = sum(len(s.text) for s in segments[window.first : window.last + 1])
        if merged and chars < MIN_WINDOW_CHARS and not window.opens:
            merged[-1].last, merged[-1].end = window.last, window.end
        else:
            window.index = len(merged)
            merged.append(window)
    return merged


def _window_text(segments: Sequence[Segment], window: WindowTopic) -> str:
    return " ".join(s.text.strip() for s in segments[window.first : window.last + 1])


def label_window(
    llm: LLMClient,
    prompts: PromptLibrary,
    segments: Sequence[Segment],
    window: WindowTopic,
    *,
    minutes: float,
    vocabulary: str,
    context: str = "",
    check: Callable[[], None] | None = None,
) -> None:
    """Ask for the window's topic and keep only what the text (``context``: the neighbouring
    windows) supports."""
    text = _window_text(segments, window)
    prompt = prompts.render(
        "chapter_window", minutes=f"{minutes:g}", vocabulary=vocabulary or "—", text=text
    )
    answer = llm.generate(prompt, _WindowAnswer, check=check).value
    indices = range(window.first, window.last + 1)
    window.words = [
        w.strip()
        for w in answer.words[:3]
        if w.strip() and find_phrases(segments, [w], "topic", WORD_THRESHOLD, indices=indices)
    ]
    reference = _tokens(text) + _tokens(context) + _tokens(vocabulary)
    label = " ".join(answer.label.split())
    if label and window.words and support(label, reference) >= MIN_SUPPORT:
        window.label, window.source = label[:1].upper() + label[1:], "llm"
    else:
        log.info("window %d: label %r is not supported by its text", window.index, label)


def _similar(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def group_windows(windows: Sequence[WindowTopic], low: int, high: int) -> list[list[int]]:
    """Merge neighbouring windows about the same topic into ``low``…``high`` groups."""
    groups = [[w.index] for w in windows]
    stems = [_stems(f"{w.label} {' '.join(w.words)}") for w in windows]

    def group_stems(group: list[int]) -> set[str]:
        return set().union(*(stems[i] for i in group))

    # A window without a label belongs to the topic before it.
    for position in range(len(groups) - 1, 0, -1):
        if not stems[groups[position][0]] and not windows[groups[position][0]].opens:
            groups[position - 1] += groups.pop(position)
    # A group without any topic (e.g. opened by a phrase but not understood) joins a neighbour.
    position = 0
    while len(groups) > 1 and position < len(groups):
        if group_stems(groups[position]):
            position += 1
        elif position == 0:
            groups[1] = groups.pop(0) + groups[0]
        else:
            groups[position - 1] += groups.pop(position)

    def best_pair() -> tuple[int, float]:
        scored = [
            (
                _similar(group_stems(groups[i]), group_stems(groups[i + 1]))
                - (1 if windows[groups[i + 1][0]].opens else 0),  # transition phrase: last
                -len(groups[i]) - len(groups[i + 1]),  # on a tie, merge the smaller pair
                i,
            )
            for i in range(len(groups) - 1)
        ]
        score, _, index = max(scored)
        return index, score

    while len(groups) > low:
        index, score = best_pair()
        if len(groups) <= high and score < MERGE_SIMILARITY:
            break
        groups[index] += groups.pop(index + 1)
    return groups


def _start_of(
    segments: Sequence[Segment],
    windows: Sequence[WindowTopic],
    group: list[int],
    phrases: Sequence[str],
    threshold: float,
) -> tuple[float, str]:
    """The group's first segment, or a transition phrase in its first window or at the end
    of the window before it."""
    first = windows[group[0]]
    if first.opens:
        return first.start, f"«{first.opens}»"
    begin = first.first
    if group[0] > 0:
        before = windows[group[0] - 1]
        begin = before.first + (before.last - before.first) * 2 // 3
    found = find_phrases(
        segments, phrases, "topic", threshold, indices=range(begin, first.last + 1)
    )
    if found:
        return found[0].start, f"«{found[0].found}»"
    return segments[first.first].start, ""


def title_group(
    llm: LLMClient,
    prompts: PromptLibrary,
    windows: Sequence[WindowTopic],
    group: list[int],
    text: str,
    *,
    chars: int,
    check: Callable[[], None] | None = None,
) -> tuple[str, str]:
    """A short title for a group of windows and where it came from ("llm" or "label")."""
    labelled = [windows[i] for i in group if windows[i].label]
    if not labelled:
        return "", "none"
    labels = "\n".join(f"- {w.label}" for w in labelled)
    words = ", ".join(dict.fromkeys(word for w in labelled for word in w.words))
    everything = set().union(*(_stems(w.label) for w in labelled))
    best = max(labelled, key=lambda w: (len(_stems(w.label) & everything), w.source == "llm")).label
    if len(labelled) == 1:
        return best, "label"
    prompt = prompts.render("chapter_title", labels=labels, words=words or "—", chars=chars)
    title = " ".join(llm.generate(prompt, _TitleAnswer, check=check).value.title.split())
    reference = _tokens(labels) + _tokens(words) + _tokens(text)
    if title and len(title) <= chars and support(title, reference) >= MIN_SUPPORT:
        return title, "llm"
    log.info("chapter title %r is not supported; using the label %r", title, best)
    return best, "label"


def _check(drafts: list[Draft], duration: float, settings: MetadataSettings) -> list[Chapter]:
    return check_chapters([Chapter(d.start, d.title) for d in drafts], duration, settings)


def generate_chapters(
    transcript: Transcription,
    duration: float,
    settings: MetadataSettings,
    *,
    llm: LLMClient,
    prompts: PromptLibrary,
    vocabulary: str = "",
    phrase_threshold: float = 0.85,
    check: Callable[[], None] | None = None,
    validate: Callable[[list[Draft], float, MetadataSettings], list[Chapter]] = _check,
) -> ChapterResult:
    """Chapters for the cleaned video, or none (with a flag) when no valid list can be made.
    ``LLMError`` from a window is counted; when most windows fail it is raised (retry)."""
    started = time.monotonic()
    segments = transcript.segments
    windows = make_windows(
        segments, settings.window_minutes * 60, settings.chapter_start_phrases, phrase_threshold
    )
    calls, errors = 0, 0
    for window in windows:
        try:
            calls += 1
            label_window(
                llm, prompts, segments, window,
                minutes=settings.window_minutes, vocabulary=vocabulary, check=check,
                context=" ".join(
                    _window_text(segments, w) for w in windows if abs(w.index - window.index) == 1
                ),
            )  # fmt: skip
        except LLMError:
            errors += 1
            window.source = "error"
            if errors > max(2, len(windows) // 2):
                raise
    low = settings.min_chapters
    high = max(low, min(MAX_CHAPTERS, round(duration / 60 / MINUTES_PER_CHAPTER)))
    details: dict[str, Any] = {"windows": [asdict(w) for w in windows]}
    labelled = sum(1 for w in windows if w.label)
    flags: list[str] = []
    chapters: list[Chapter] = []
    if labelled >= low:
        groups = group_windows(windows, low, high)
        drafts, notes = [], []
        for group in groups:
            start, phrase = _start_of(
                segments, windows, group, settings.chapter_start_phrases, phrase_threshold
            )
            text = " ".join(_window_text(segments, windows[i]) for i in group)
            calls += 1 if sum(1 for i in group if windows[i].label) > 1 else 0
            title, source = title_group(
                llm, prompts, windows, group, text,
                chars=settings.max_chapter_title_chars, check=check,
            )  # fmt: skip
            if drafts and start < drafts[-1].start + settings.min_chapter_seconds:
                start, phrase = segments[windows[group[0]].first].start, ""  # keep the order
            drafts.append(Draft(0.0 if not drafts else start, title))
            notes.append({"windows": group, "start": start, "phrase": phrase, "title_from": source})
        details["groups"] = notes
        try:
            chapters = validate(drafts, duration, settings)
        except ChapterError as exc:
            details["rejected"] = str(exc)
            log.warning("chapters rejected: %s", exc)
    else:
        details["rejected"] = f"only {labelled} of {len(windows)} windows have a usable topic"
    if not chapters:
        flags.append(NOT_GENERATED)
    details["seconds"] = round(time.monotonic() - started, 1)
    details["llm_errors"] = errors
    return ChapterResult(chapters, flags, calls, details)


def topic_summary(result: ChapterResult, *, limit: int = 5, prefix: str = "Թեմաներ՝ ") -> str:
    """A short line about the lesson's topics for the description, Slack and Classroom: the
    chapter titles, or the most frequent checked window labels when there are no chapters.
    Built from already checked texts, so nothing new is invented (FR-16)."""
    if result.chapters:
        topics = [c.title for c in result.chapters]
    else:
        counts: dict[str, int] = {}
        for window in result.details.get("windows", []):
            if window.get("label"):
                counts[window["label"]] = counts.get(window["label"], 0) + 1
        topics = sorted(counts, key=lambda label: -counts[label])
    topics = list(dict.fromkeys(topics))[:limit]
    return prefix + ", ".join(topics) + "։" if topics else ""
