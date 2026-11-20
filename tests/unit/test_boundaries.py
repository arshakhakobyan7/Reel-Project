"""Lesson start/end detection (T-402) on scripted transcripts."""

from pathlib import Path

import pytest

from reel.cleaning.boundaries import (
    CONFLICT,
    END_NOT_FOUND,
    LLM_TEXT_LIMIT,
    START_NOT_FOUND,
    detect_boundaries,
    find_phrases,
    normalize,
    phonetic,
)
from reel.config.prompts import PromptLibrary
from reel.config.schema import BoundarySettings
from reel.llm import FakeLLM
from reel.stt import Segment, Transcription, scripted_lesson
from reel.stt.base import LESSON_MARKS

PROMPTS = PromptLibrary(Path(__file__).resolve().parents[2] / "config" / "prompts")
NO_LLM = BoundarySettings(use_llm=False)


def lesson(*lines: tuple[float, str], end: float = 7200.0) -> Transcription:
    """Segments at the given minutes; each lasts 20 s. A last filler segment sets the end."""
    segments = [Segment(m * 60, m * 60 + 20, text) for m, text in lines]
    segments.append(Segment(end - 20, end, "..."))
    return Transcription("hy", tuple(sorted(segments, key=lambda s: s.start)))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Բարև Ձեզ, ՍԿՍՈՒՄ ԵՆՔ։", "բարեւ ձեզ սկսում ենք"),
        ("Լսու՞մ եք ինձ", "լսում եք ինձ"),
        ("և, եւ", "եւ եւ"),
        ("  Hello,   everyone! ", "hello everyone"),
    ],
)
def test_armenian_aware_normalisation(text, expected):
    assert normalize(text) == expected


def test_letters_that_stt_confuses_are_folded_for_matching():
    assert phonetic("մեթոդ") == phonetic("մեթոտ")
    assert phonetic("Գործնական") == phonetic("կործնական")
    assert phonetic("ֆունկցիա") == phonetic("վունկցիա")
    assert phonetic("Այսօր") == phonetic("այսոր") and phonetic("և") == phonetic("եվ")


def test_the_scripted_lesson_is_found():
    duration = 3600.0
    transcript = Transcription("hy", scripted_lesson(duration))
    result = detect_boundaries(transcript, NO_LLM, duration=duration)
    assert result.start.seconds == pytest.approx(LESSON_MARKS["greeting"] * duration)
    assert result.start.method == "greeting + topic"
    assert result.end.seconds == pytest.approx(LESSON_MARKS["closing_end"] * duration)
    assert result.flags == () and result.llm_calls == 0


@pytest.mark.parametrize(
    ("greeting", "topic", "closing"),
    [
        ("Բարև ձեզ։", "Այսօր կխոսենք ցիկլերի մասին։", "Այսօրվա համար այսքանը։"),  # exact
        ("Բարեվ ձեզ։", "Այսոր կխոսինք ցիկլերի մասին։", "Այսորվա համար այսքանն է։"),  # misspelt
        (
            "Բարի երեկո բոլորին",
            "Մեր այսօրվա թեման ցիկլերն են",
            "Շնորհակալություն ուշադրության համար",
        ),
        ("Good evening!", "Today we will talk about loops.", "See you next time!"),
    ],
)
def test_exact_misspelt_and_english_phrases(greeting, topic, closing):
    transcript = lesson(
        (2, "Լսու՞մ եք ինձ, մի րոպե։"),
        (8, greeting),
        (9, topic),
        (60, "Հիմա for ցիկլը։"),
        (110, closing),
        (113, "Ով հարց ունի, թող մնա։"),
    )
    result = detect_boundaries(transcript, NO_LLM)
    assert (result.start.seconds, result.end.seconds) == (8 * 60, 110 * 60 + 20)
    assert result.flags == ()


def test_a_students_hello_during_the_chatter_does_not_win():
    transcript = lesson(
        (1, "Բարև, Արամ, ինչպե՞ս ես։"),  # a student, long before the lecture
        (3, "Մի րոպե, սպասենք մյուսներին։"),
        (12, "Լավ, բարև ձեզ։"),  # the lecturer
        (13, "Այսօր կանցնենք ֆունկցիաները։"),
        (110, "Այսօր այսքանը։"),
    )
    result = detect_boundaries(transcript, NO_LLM)
    assert result.start.seconds == 12 * 60
    assert [m.kind for m in result.start.evidence] == ["greeting", "topic"]


def test_a_topic_opener_alone_is_not_enough_without_the_llm():
    transcript = lesson((5, "Միկրոֆոնը աշխատում է։"), (7, "Սկսենք դասը։"), (110, "Կհանդիպենք։"))
    result = detect_boundaries(transcript, NO_LLM)
    assert result.start is None and result.flags == (START_NOT_FOUND,)


def test_the_llm_confirms_a_topic_opener():
    transcript = lesson((5, "Միկրոֆոնը աշխատում է։"), (7, "Սկսենք դասը։"), (110, "Կհանդիպենք։"))
    llm = FakeLLM(
        [
            {
                "found": True,
                "segment": 1,
                "quote": "Սկսենք դասը",
                "evidence": "դասախոսը սկսում է դասը",
            }
        ]
    )
    result = detect_boundaries(transcript, BoundarySettings(), llm=llm, prompts=PROMPTS)
    assert (result.start.seconds, result.start.method) == (7 * 60, "llm")
    assert [m.kind for m in result.start.evidence] == ["topic"]


RECAP = (
    (0, "Նախորդ դասին խոսեցինք dictionary-ների և hash table-ի մասին"),
    (1.5, "Օբյեկտը hash ֆունկցիայի միջոցով ստանում է ինդեքս"),
    (19, "Այսօր մենք անցնելու ենք մեր սեփական ֆունկցիաներին"),
    (110, "Այսօր այսքանը"),
)


def test_a_recording_that_starts_with_teaching_is_not_cut():
    """The real sample lesson: a 20-minute recap, then "այսօր անցնելու ենք ..."."""
    llm = FakeLLM([{"found": False, "evidence": "ողջույն չկա, դասը սկսվում է կրկնությամբ"}])
    result = detect_boundaries(lesson(*RECAP), BoundarySettings(), llm=llm, prompts=PROMPTS)
    assert result.start is None and result.flags == (START_NOT_FOUND,)


def test_an_invented_quote_is_rejected():
    """Seen on the real lesson: the LLM named line 1 and claimed it says «Բարև ձեզ»."""
    llm = FakeLLM([{"found": True, "segment": 1, "quote": "Բարև ձեզ", "evidence": "ողջույն"}])
    result = detect_boundaries(lesson(*RECAP), BoundarySettings(), llm=llm, prompts=PROMPTS)
    assert result.start is None and result.flags == (START_NOT_FOUND,)


def test_the_last_closing_ends_the_lesson():
    transcript = lesson(
        (8, "Բարև ձեզ"),
        (9, "Այսօր կխոսենք ցուցակների մասին"),
        (100, "Շնորհակալություն, Անի"),  # a thanks before the real end
        (112, "Այսօրվա համար այսքանը, կհանդիպենք հաջորդ դասին"),
    )
    assert detect_boundaries(transcript, NO_LLM).end.seconds == 112 * 60 + 20


def test_phrases_outside_the_search_windows_are_ignored():
    settings = BoundarySettings(use_llm=False, start_search_minutes=10, end_search_minutes=10)
    transcript = lesson(
        (15, "Բարև ձեզ"), (16, "Այսօր կխոսենք ցիկլերի մասին"), (60, "Այսօր այսքանը")
    )
    result = detect_boundaries(transcript, settings)
    assert result.start is None and result.end is None
    assert result.flags == (START_NOT_FOUND, END_NOT_FOUND)


def test_no_phrases_means_no_cut_and_flags():
    transcript = lesson((5, "Միկրոֆոնը աշխատում է"), (60, "Հիմա for ցիկլը"), (110, "Լավ"))
    result = detect_boundaries(transcript, NO_LLM)
    assert (result.start, result.end, result.flags) == (
        None,
        None,
        (START_NOT_FOUND, END_NOT_FOUND),
    )


PARAPHRASED = (
    (3, "Մի րոպե, սպասենք։"),
    (9, "Եկեք անցնենք մեր պարապմունքին"),  # not in the phrase lists
    (60, "Հիմա for ցիկլը"),
    (111, "Վերջացրինք, մինչ երկուշաբթի"),
)
PARAPHRASE_ANSWERS = [
    {"found": True, "segment": 1, "quote": "անցնենք մեր պարապմունքին", "evidence": "սկսում է"},
    {"found": True, "segment": 3, "quote": "Վերջացրինք, մինչ երկուշաբթի", "evidence": "հրաժեշտ"},
]


def test_the_llm_alone_never_cuts():
    """D-059: the line the LLM names must hold one of the configured phrases."""
    llm = FakeLLM(PARAPHRASE_ANSWERS)
    result = detect_boundaries(lesson(*PARAPHRASED), BoundarySettings(), llm=llm, prompts=PROMPTS)
    assert (result.start, result.end) == (None, None)
    assert result.flags == (START_NOT_FOUND, END_NOT_FOUND) and result.llm_calls == 2


def test_a_recording_that_starts_with_the_teaching_keeps_its_beginning():
    """The first 15 minutes of the real lesson: the recording starts with a recap, and the
    LLM named a teaching line ("now let's take an example") as a greeting; 2:48 were cut."""
    transcript = lesson(
        (0, "Նախորդ դասին խոսքը վերաբերվում էր դիկշներին"),
        (1, "Hashtable-ը հանդիսանում էր հատուկ դատաստրուկտուրա"),
        (2.8, "բարձը, իմա որինակ նույն է եկեք բերենք"),
        (12, "Հիմա դիտարկենք ֆունկցիաները"),
    )
    llm = FakeLLM([
        {"found": True, "segment": 2, "quote": "բարձը, իմա որինակ նույն է եկեք բերենք",
         "evidence": "Այս տողը ողջույնի արտահայտություն է"},
        {"found": False},
    ])  # fmt: skip
    result = detect_boundaries(transcript, BoundarySettings(), llm=llm, prompts=PROMPTS)
    assert result.start is None and START_NOT_FOUND in result.flags


def test_paraphrases_are_judged_by_the_llm_once_they_are_in_the_phrase_lists():
    transcript = lesson(*PARAPHRASED)
    settings = BoundarySettings(
        greeting_phrases=(*BoundarySettings().greeting_phrases, "անցնենք մեր պարապմունքին"),
        closing_phrases=(*BoundarySettings().closing_phrases, "մինչ երկուշաբթի"),
    )
    llm = FakeLLM(PARAPHRASE_ANSWERS)
    result = detect_boundaries(transcript, settings, llm=llm, prompts=PROMPTS)
    assert (result.start.seconds, result.start.method) == (9 * 60, "llm")
    assert result.start.note == "«անցնենք մեր պարապմունքին» — սկսում է"
    assert (result.end.seconds, result.end.method) == (111 * 60 + 20, "closing")  # a phrase now
    assert result.llm_calls == 1
    start_prompt = llm.calls[0]["prompt"]
    assert "[1] 0:09:00 Եկեք անցնենք մեր պարապմունքին" in start_prompt
    assert "բարև ձեզ" in start_prompt  # the phrase list is part of the prompt


@pytest.mark.parametrize(
    "answer",
    [
        {"found": False, "evidence": "չկա"},
        {"found": True, "segment": 99},  # not one of the listed segments
    ],
)
def test_an_unsure_or_invalid_llm_answer_means_no_cut(answer):
    transcript = lesson((5, "Միկրոֆոնը աշխատում է"), (110, "Այսօր այսքանը"))
    result = detect_boundaries(
        transcript, BoundarySettings(), llm=FakeLLM([answer]), prompts=PROMPTS
    )
    assert result.start is None and result.flags == (START_NOT_FOUND,)


def test_a_start_after_the_end_cuts_nothing():
    transcript = lesson(
        (99, "Բարև ձեզ"), (100, "Այսօր կխոսենք ամփոփման մասին"), (90, "Այսօր այսքանը"), end=6000
    )
    settings = BoundarySettings(use_llm=False, start_search_minutes=120, end_search_minutes=120)
    result = detect_boundaries(transcript, settings)
    assert (result.start, result.end) == (None, None)
    assert result.flags == (CONFLICT, START_NOT_FOUND, END_NOT_FOUND)


def test_fuzzy_threshold_comes_from_config():
    segments = (Segment(0, 5, "Բարեվ ձեզս"),)  # one extra letter
    assert find_phrases(segments, ["բարև ձեզ"], "greeting", 0.85)
    assert not find_phrases(segments, ["բարև ձեզ"], "greeting", 0.99)


def test_a_long_window_gives_the_llm_the_first_lines_for_the_start_and_the_last_for_the_end():
    """Seen on the real lesson: the last 30 minutes have more text than the LLM gets, and the
    farewell is in the very last line."""
    talk = " ".join(["Հիմա for ցիկլի մեջ փոփոխականը մեծանում է ամեն անգամ։"] * 3)
    minutes = [(m / 2, f"{talk} ({m})") for m in range(1, 239)]  # every 30 s, two hours
    transcript = lesson(*minutes, (119.5, "Վերջացրինք, մինչ երկուշաբթի"), end=7200)
    assert sum(len(s.text) for s in transcript.segments if s.end > 5400) > LLM_TEXT_LIMIT
    llm = FakeLLM([{"found": False}, {"found": False}])
    detect_boundaries(transcript, BoundarySettings(), llm=llm, prompts=PROMPTS)
    start_prompt, end_prompt = (call["prompt"] for call in llm.calls)
    assert "(1)" in start_prompt and "(59)" not in start_prompt
    assert "Վերջացրինք, մինչ երկուշաբթի" in end_prompt and "(238)" in end_prompt
    assert "(181)" not in end_prompt  # the start of the end window is left out
    assert end_prompt.index("(238)") < end_prompt.index("Վերջացրինք")  # still in time order
