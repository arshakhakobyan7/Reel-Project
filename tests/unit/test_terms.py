"""English programming terms in Armenian transcripts stay English (T-1010, D-062).

The Armenian-letter spellings below are what Whisper wrote in real lessons.
"""

import pytest

from reel.stt import Segment, Transcription
from reel.stt.terms import TERMS, TermFixer, fold, merge_terms, whisper_prompt

FIXER = TermFixer(TERMS)


def fixed(text):
    return FIXER.fix(text)[0]


@pytest.mark.parametrize(
    ("heard", "written"),
    [
        ("խոսքը վերաբերվում է դիկշների մասին", "խոսքը վերաբերվում է dictionary-ների մասին"),
        ("մեր լիստի մեջ", "մեր list-ի մեջ"),
        ("ստեկը լցվում է", "stack-ը լցվում է"),
        ("վունկթյայի մեջ", "function-ի մեջ"),  # the "յ" glide is dropped
        ("վունկթյան մեջ", "function-ն մեջ"),
        ("հեշվունկթյայից գեներացնում էր", "hash function-ից գեներացնում էր"),
        ("պետք է լինեն հեշպըլ", "պետք է լինեն hashable"),
        ("չկա այդ հեշը բլությունը", "չկա այդ hashable-ությունը"),
        ("իսկ մյութըբը թայփերի ժամանակ", "իսկ mutable type-երի ժամանակ"),
        ("պայթնում ամեն ինչ ոբեկտ է", "Python-ում ամեն ինչ object է"),
        ("ստեկ վրեմը", "stack frame-ը"),
        ("ստեկֆրեմները", "stack frame-ները"),
        ("վրեմը", "frame-ը"),  # folded: the ending is kept
        ("դատաստրուկտուրա", "data structure"),
        ("դատաստրուկտուրը", "data structure-ը"),
        ("մեր սլոտների սայզի վրա", "մեր slot-ների size-ի վրա"),
        ("թապլը", "tuple-ը"),
        ("ռեվերանսով", "reference-ով"),
    ],
)
def test_real_spellings_become_english(heard, written):
    assert fixed(heard) == written


def test_a_following_word_is_never_taken_for_an_ending():
    assert fixed("հեշտեպլը ու անցնենք") == "hash table-ը ու անցնենք"
    assert fixed("դատաստրուկտուրա այդ մեջ") == "data structure այդ մեջ"
    assert fixed("մեթոդը ու") == "method-ը ու"


@pytest.mark.parametrize(
    "armenian",
    [
        "հեշտ է հասկանալ",  # "easy", not "hash"
        "դաբլ հեշինք",  # "double": folded, it would be "թապլ" (tuple); short words are exact
        "պոպոխականները",  # Armenian (a misspelled "փոփոխական"), not an English term
        "օբյեկտիվ գնահատական",
        "կոլեկտիվ աշխատանք",
        "ֆունկցիոնալ ծրագրավորում",
    ],
)
def test_armenian_words_stay(armenian):
    assert fixed(armenian) == armenian


def test_capitals_at_the_start_of_a_sentence():
    assert fixed("Հեշտ թեբլը ինքը։ Ստեկը լցվեց") == "Hash table-ը ինքը։ Stack-ը լցվեց"
    assert fixed("մեր Սայզի վրա") == "մեր size-ի վրա"  # Whisper's capital inside a sentence


def test_latin_misses_are_fixed_too():
    assert fixed("Hesh-table-ը և diction-ների") == "Hash table-ը և dictionary-ների"
    assert fixed("կատարում է OpenAdressing") == "կատարում է open addressing"
    assert fixed("hash table և immutable type-եր") == "hash table և immutable type-եր"


def test_what_was_changed_is_counted():
    text, found = FIXER.fix("լիստի և լիստի, ոբեկտ")
    assert text == "list-ի և list-ի, object"
    assert found == {("լիստի", "list-ի"): 2, ("ոբեկտ", "object"): 1}


def test_folding_and_ambiguous_spellings():
    assert fold("թայփ") == fold("տայպ") == fold("դայբ")
    assert fold("ոբբեկտ") == fold("օբեկտ")
    # two terms whose long spellings fold alike: only the exact spellings count
    fixer = TermFixer({"alpha": ("կապասա",), "beta": ("գաբասա",)})
    assert fixer.fix("կապասա գաբասա քափասա")[0] == "alpha beta քափասա"


def test_config_spellings_add_and_drop_terms():
    merged = merge_terms({"call stack": ["կոլ ստեկ"], "object": [], "list": ["լիսթ"]})
    assert "object" not in merged and merged["call stack"] == ("կոլ ստեկ",)
    assert merged["list"] == ("լիստ", "լիսթ")
    assert TermFixer(merged).fix("կոլ ստեկը, ոբեկտ, լիսթը")[0] == "call stack-ը, ոբեկտ, list-ը"


def test_a_transcription_is_fixed_segment_by_segment():
    transcription = Transcription(
        "hy", (Segment(0, 2, "Բարև ձեզ"), Segment(2, 5, "մեր լիստը")), "fake", {}
    )
    result, found = FIXER.fix_transcription(transcription)
    assert [s.text for s in result.segments] == ["Բարև ձեզ", "մեր list-ը"]
    assert result.segments[0] is transcription.segments[0] and sum(found.values()) == 1


def test_the_whisper_prompt():
    prompt = whisper_prompt("Օրինակ Python-ով։", "list, Python, stack", "python, LIFO, ")
    assert prompt == "Օրինակ Python-ով։ list, Python, stack, LIFO"  # group words first, once
    assert whisper_prompt("", "", "") == ""
