"""Chapter generation (T-501) with a scripted LLM."""

from pathlib import Path

import pytest

from reel.config.prompts import PromptLibrary
from reel.config.schema import MetadataSettings
from reel.llm import FakeLLM, LLMError
from reel.metadata.chapters import NOT_GENERATED, generate_chapters, make_windows, support
from reel.stt import Segment, Transcription

PROMPTS = PromptLibrary(Path(__file__).resolve().parents[2] / "config" / "prompts")
SETTINGS = MetadataSettings()
DICT = "Dictionary-ի մեթոդները՝ keys և values, dictionary-ն պահում է զույգեր։"
LOOP = "For ցիկլը անցնում է list-ի տարրերով, ամեն քայլին for ցիկլը վերցնում է հաջորդը։"
FUNC = "Ֆունկցիան սահմանում ենք def բառով, ֆունկցիան վերադարձնում է return արժեքը։"
TRANSITION = "Լավ, հիմա անցնենք ֆունկցիաներին։"


def lesson(minutes: float = 30, transition_at: float = 20) -> Transcription:
    """Dictionaries until 9:00, loops until 20:00, then «հիմա անցնենք» and functions."""
    segments = []
    t = 0.0
    while t < minutes * 60:
        if abs(t - transition_at * 60) < 1e-6:
            text = TRANSITION
        else:
            text = DICT if t < 540 else LOOP if t < transition_at * 60 else FUNC
        segments.append(Segment(t, t + 18, text))
        t += 20
    return Transcription("hy", tuple(segments), "fake")


def topic_of(prompt: str) -> dict:
    if "def բառով" in prompt:
        return {"label": "Ֆունկցիաների սահմանում", "words": ["ֆունկցիան", "def"]}
    if "For ցիկլը" in prompt:
        return {"label": "For ցիկլ list-ի վրա", "words": ["for ցիկլը", "list-ի"]}
    return {"label": "Dictionary-ի մեթոդներ", "words": ["dictionary-ն", "keys"]}


def answers(*, label=topic_of, title=None):
    def answer(prompt, schema):
        if "title" in schema.model_fields:
            return title(prompt) if title else {"title": prompt.split("\n- ")[1].split("\n")[0]}
        return label(prompt)

    return [answer] * 100


def run(transcript, *, duration=None, llm=None):
    return generate_chapters(
        transcript,
        duration or transcript.end,
        SETTINGS,
        llm=llm or FakeLLM(answers()),
        prompts=PROMPTS,
        vocabulary="Python, list, dictionary, for, def",
    )


def test_three_topics_give_three_chapters_with_exact_starts():
    llm = FakeLLM(answers())
    result = run(lesson(), llm=llm)
    assert result.flags == []
    assert [(c.start, c.title) for c in result.chapters] == [
        (0, "Dictionary-ի մեթոդներ"),
        (540, "For ցիկլ list-ի վրա"),
        (1200, "Ֆունկցիաների սահմանում"),  # exactly at «հիմա անցնենք»
    ]
    assert result.details["groups"][2]["phrase"] == "«հիմա անցնենք»"
    window_prompt = llm.calls[0]["prompt"]
    assert "Python, list, dictionary" in window_prompt and DICT in window_prompt
    assert result.llm_calls == len(llm.calls)


def test_windows_follow_the_minutes_and_open_at_a_transition_phrase():
    windows = make_windows(lesson().segments, 180, SETTINGS.chapter_start_phrases)
    assert [w.start for w in windows] == [0, 180, 360, 540, 720, 900, 1080, 1200, 1380, 1560, 1740]
    assert windows[7].opens == "հիմա անցնենք"


def test_an_invented_label_is_not_used():
    """Seen on the real lesson: the small model wrote words that are not in the text."""

    def inventing(prompt):
        if "For ցիկլը" in prompt:
            return {"label": "Վարկածային դասեղաններ", "words": ["for ցիկլը"]}
        return topic_of(prompt)

    result = run(lesson(), llm=FakeLLM(answers(label=inventing)))
    labels = {w["label"] for w in result.details["windows"]}
    assert "Վարկածային դասեղաններ" not in labels
    # the loop windows have no topic now: dictionaries, then functions — too few chapters
    assert result.chapters == [] and result.flags == [NOT_GENERATED]


def test_an_invented_title_is_replaced_by_the_best_label():
    result = run(lesson(), llm=FakeLLM(answers(title=lambda p: {"title": "Տիեզերական նավեր"})))
    assert [c.title for c in result.chapters] == [
        "Dictionary-ի մեթոդներ",
        "For ցիկլ list-ի վրա",
        "Ֆունկցիաների սահմանում",
    ]
    assert {g["title_from"] for g in result.details["groups"]} == {"label"}


def test_too_few_topics_mean_no_chapters_and_a_flag():
    result = run(lesson(minutes=5))
    assert result.chapters == [] and result.flags == [NOT_GENERATED]
    assert "windows have a usable topic" in result.details["rejected"]


def test_chapters_outside_the_video_are_rejected():
    result = run(lesson(), duration=1100)  # the functions chapter would start at 20:00
    assert result.chapters == [] and result.flags == [NOT_GENERATED]
    assert result.details["rejected"]


def test_an_unparseable_answer_for_one_window_is_survived():
    replies = answers()
    replies.insert(0, "this is not JSON")
    result = run(lesson(), llm=FakeLLM(replies))
    assert result.details["llm_errors"] == 1 and result.details["windows"][0]["source"] == "error"
    assert len(result.chapters) == 3


def test_when_most_windows_fail_the_error_is_raised_for_a_retry():
    with pytest.raises(LLMError):
        run(lesson(), llm=FakeLLM(["not JSON"] * 100))


def test_support_counts_words_found_in_the_text_with_recognition_errors():
    reference = ["վունկցիաները", "սահմանում", "ենք", "def", "բառով"]
    from reel.cleaning.boundaries import phonetic

    reference = [phonetic(w) for w in reference]
    assert support("Ֆունկցիաների սահմանում", reference) == 1.0  # ֆ/վ folded, inflection
    assert support("Տիեզերական նավեր", reference) == 0.0


def test_the_topic_summary_uses_only_checked_texts():
    from reel.metadata.chapters import topic_summary

    result = run(lesson())
    assert topic_summary(result) == (
        "Թեմաներ՝ Dictionary-ի մեթոդներ, For ցիկլ list-ի վրա, Ֆունկցիաների սահմանում։"
    )
    few = run(lesson(minutes=5))  # no chapters: the window labels
    assert topic_summary(few) == "Թեմաներ՝ Dictionary-ի մեթոդներ։"
