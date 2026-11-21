"""English programming terms in Armenian transcripts (T-1010, D-062).

Told that the speech is Armenian, Whisper writes the English words a lecturer uses in
Armenian letters, and differently each time: "hash table" came out as "հեշտեբլ", "հեշտեպլ"
and "հեշ թե բլ" in one lesson. The user's rule: the terms stay English. Two steps:

1. ``whisper_prompt`` builds the prompt for the first 30-second window: an Armenian sentence
   that writes such terms in Latin letters, then the vocabulary. Sent to every window
   ("hotwords") it put more terms in Latin letters, but it takes room from the speech: a
   real lesson lost 10-25 % of its words, so that is off by default ([stt] hotwords).
2. ``TermFixer`` replaces the Armenian-letter spellings with the English term and keeps the
   Armenian ending after a hyphen: "դիկշների" -> "dictionary-ների",
   "վունկթյայի" -> "function-ի". On 4 minutes of a lesson it fixed 38 terms.

Whisper mixes up letters that sound alike in Armenian speech (պ/բ/փ, տ/դ/թ, կ/գ/ք, ...), so
spellings of five letters or more are compared with those letters folded together. Shorter
spellings must match exactly: folded, "թապլ" (tuple) and "դաբլ" (double) would be the same.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import replace

from reel.stt.transcript import Transcription

# The spellings were taken from real lessons (T-1002, T-1009). A spelling with spaces matches
# the same words with or without spaces between them. Latin spellings fix Latin-letter misses.
TERMS: dict[str, tuple[str, ...]] = {
    "dictionary": ("դիկշ", "դիքշ", "դիկշնարի", "դիքշնարի", "diction"),
    "dict": ("դիկտ",),
    "hash table": (
        "հեշտեբլ",
        "հեշտեպլ",
        "հեշթեբլ",
        "հեշտեյբլ",
        "հեշտեբել",
        "հեշտ թեբլ",
        "հեշտ հեպլ",
        "հեշ թե բլ",
        "hesh-table",
        "hash-table",
    ),
    "hash function": (
        "հեշվունկթյա",
        "հեշվունթյա",
        "հեշպունթյա",
        "հեշվունկցիա",
        "հեշֆունկցիա",
        "հեշվանքշն",
        "հեշ ֆունկցիա",
        "հեշ վունկթյա",
        "հեշ վունգթիա",
    ),
    "hash": ("հեշ", "hesh"),
    "hashable": ("հեշպըլ", "հեշպլ", "հեշբլ", "հեշը բլ"),
    "mutable": ("մյութպըլ", "մյութըբըլ", "մյութաբլ", "մյությբյլ", "մյութըբը"),
    "immutable": ("իմյութպըլ", "իմյութաբլ", "իմյութըբըլ"),
    "string": ("ստրինգ",),
    "list": ("լիստ",),
    "tuple": ("թապլ", "տապլ", "թյուպլ"),
    "slot": ("սլոտ", "սլոթ", "ծլոտ", "լոտ"),
    "bucket": ("բակետ",),
    "size": ("սայզ",),
    "type": ("թայփ", "թայպ", "տայպ"),
    "data type": ("դատաթայպ", "դատատայփ", "դատատայպ"),
    "data structure": (
        "դատաստրուկտուրա",
        "դատաստրուկտուր",
        "դատա ստրուկտուրա",
        "դատա ստրուկտուր",
        "data-structure",
    ),
    "object": ("օբյեկտ", "ոբյեկտ", "ոբեկտ", "օբեկտ", "objekt"),
    "class": ("կլաս", "քլաս"),
    "first-class": ("վրծկլաս", "վրսկլաս", "ֆըրստ քլաս"),
    "function": ("ֆունկցիա", "ֆունկթյա", "ֆունկթիա", "ֆունկցյա"),
    "method": ("մեթոդ",),
    "index": ("ինդեքս", "ինդեջ"),
    "reference": ("ռեվերանս", "հեվերանս", "ռեֆերանս", "ռեֆերենս", "ռեվերանց"),
    "collection": ("կլեկշն", "քլեկշն", "կոլեկշն", "կլեկշըն", "կլեկշոն"),
    "capacity": ("կափասիտի", "կապասիտի"),
    "stack": ("ստեկ", "ստեք", "սթեք", "սթեկ", "stec"),
    "stack frame": ("ստեկֆրեմ", "ստեկֆրեն", "ստեկ ֆրեմ"),
    "frame": ("ֆրեմ", "ֆրեյմ"),
    "call": ("կոլ",),
    "recursion": ("ռեկուրսիա", "ռեկուրսյա"),
    "recursive": ("ռեկուրսիվ",),
    "error": ("էրոր", "էռոր"),
    "input": ("ինպուտ", "ինփութ"),
    "output": ("աութպուտ", "աուտպուտ"),
    "print": ("պրինտ", "փրինտ"),
    "key-value": ("q-value", "կի վալու", "կի վելյու"),
    "open addressing": ("openadressing",),
    "Python": ("պայթն", "պայթոն", "պայթեն", "փայթն", "պայթըն"),
}

# Armenian endings after a term, longest first. A "յ" glide is dropped after the English
# word ("ֆունկցիայի" -> "function-ի").
_ENDINGS = (
    "", "ը", "ն", "ի", "ին", "ից", "ով", "ում", "ու", "ա", "դ", "ս",
    "եր", "երը", "երն", "երի", "երին", "երից", "երով", "երում", "երս", "երդ",
    "ներ", "ները", "ներն", "ների", "ներին", "ներից", "ներով", "ներում", "ներս", "ներդ",
    "յի", "յին", "յից", "յով", "յում", "ություն", "ությունը", "ության",
)  # fmt: skip
ENDINGS = tuple(sorted(set(_ENDINGS), key=len, reverse=True))
_FOLD = str.maketrans({
    "պ": "բ", "փ": "բ", "տ": "դ", "թ": "դ", "կ": "գ", "ք": "գ", "ծ": "ձ", "ց": "ձ",
    "ճ": "ջ", "չ": "ջ", "ռ": "ր", "է": "ե", "օ": "ո", "ֆ": "վ", "ը": None, "յ": None,
    "ւ": None,
})  # fmt: skip
_DOUBLE = re.compile(r"(.)\1+")
_ARMENIAN = re.compile(r"[Ա-և]+")
_LATIN = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
_SENTENCE_START = re.compile(r"(^|[.։?!՞:]\s*)$")
FOLD_MIN = 5  # letters a spelling needs before its folded form counts
FOLDED_MIN = 4  # folded letters a word part needs to be compared folded
WINDOW = 3  # words


def fold(text: str) -> str:
    return _DOUBLE.sub(r"\1", text.lower().translate(_FOLD))


def merge_terms(extra: Mapping[str, Iterable[str]]) -> dict[str, tuple[str, ...]]:
    """The built-in spellings plus ``[stt.terms]``; an empty list there drops a term."""
    merged = dict(TERMS)
    for term, spellings in extra.items():
        found = tuple(spellings)
        if found:
            merged[term] = (*merged.get(term, ()), *found)
        else:
            merged.pop(term, None)
    return merged


def whisper_prompt(example: str, *vocabularies: str) -> str:
    """The example sentence, then the vocabularies (group first) without repeated words."""
    words: list[str] = []
    for vocabulary in vocabularies:
        for word in vocabulary.split(","):
            word = word.strip()
            if word and word.casefold() not in {w.casefold() for w in words}:
                words.append(word)
    return " ".join(part for part in (example.strip(), ", ".join(words)) if part)


class TermFixer:
    def __init__(self, terms: Mapping[str, Iterable[str]]) -> None:
        self.exact: dict[str, str] = {}  # one-word spellings, Armenian or Latin
        self.multi: list[tuple[str, tuple[str, ...]]] = []  # spellings of several words
        folded: dict[str, set[str]] = {}
        for term, spellings in terms.items():
            for spelling in spellings:
                parts = tuple(spelling.lower().split())
                if len(parts) > 1:
                    self.multi.append((term, parts))
                elif parts:
                    self.exact.setdefault(parts[0], term)
                    if len(parts[0]) >= FOLD_MIN and _ARMENIAN.fullmatch(parts[0]):
                        folded.setdefault(fold(parts[0]), set()).add(term)
        # A folded form that two terms share is ambiguous: only exact spellings count then.
        self.folded = {key: next(iter(t)) for key, t in folded.items() if len(t) == 1}
        self.multi.sort(key=lambda item: len(item[1]), reverse=True)

    @staticmethod
    def _splits(word: str) -> list[tuple[str, str]]:
        """(stem, ending): the whole word, then the longest ending first."""
        splits = [(word, "")]
        for ending in ENDINGS:
            if ending and word.endswith(ending) and len(word) - len(ending) >= 2:
                splits.append((word[: len(word) - len(ending)], ending))
        return splits

    def _ending(self, word: str, exact, folded) -> tuple[str, str] | None:
        """(ending, term) for one word. Exact spellings win, the whole word first, so
        "մյութըբը" is the spelling itself. Folded spellings try the longest ending first,
        because folding drops "ը": "վրեմը" is "ֆրեմ" + "ը", not "ֆրեմ"."""
        splits = self._splits(word.lower())
        for stem, ending in splits:
            term = exact(stem)
            if term is not None:
                return ending, term
        for stem, ending in [*splits[1:], splits[0]]:
            if len(fold(stem)) >= FOLDED_MIN:
                term = folded(stem)
                if term is not None:
                    return ending, term
        return None

    def _single(self, word: str) -> tuple[str, str] | None:
        return self._ending(word, self.exact.get, lambda stem: self.folded.get(fold(stem)))

    def _multi(self, words: list[str]) -> tuple[int, str, str] | None:
        """(words used, ending, term) for a spelling of several words, compared word by word,
        so that a following word ("ու", "այդ") is never taken for an ending."""
        lowered = [word.lower() for word in words]
        for term, parts in self.multi:
            size = len(parts)
            if size > len(lowered):
                continue
            head = zip(lowered[: size - 1], parts[:-1], strict=True)
            if not all(a == b or fold(a) == fold(b) for a, b in head):
                continue
            last = parts[-1]
            found = self._ending(
                lowered[size - 1],
                lambda stem, last=last, term=term: term if stem == last else None,
                lambda stem, last=last, term=term: term if fold(stem) == fold(last) else None,
            )
            if found is not None:
                return size, found[0], term
        return None

    def _armenian(self, text: str, found: Counter) -> str:
        words = list(_ARMENIAN.finditer(text))
        out, position, index = [], 0, 0
        while index < len(words):
            joined = 1  # how many words from here are separated by single spaces
            while (
                index + joined < len(words)
                and joined < WINDOW
                and text[words[index + joined - 1].end() : words[index + joined].start()] == " "
            ):
                joined += 1
            texts = [w.group() for w in words[index : index + joined]]
            match = self._multi(texts) if joined > 1 else None
            if match is None:
                single = self._single(texts[0])
                match = (1, *single) if single else None
            if match is None:
                index += 1
                continue
            size, ending, term = match
            first, last = words[index], words[index + size - 1]
            if ending.startswith("յ"):
                ending = ending[1:]
            if first.group()[0].isupper() and _SENTENCE_START.search(text[: first.start()]):
                term = term[0].upper() + term[1:]
            replacement = term + (f"-{ending}" if ending else "")
            found[(text[first.start() : last.end()], replacement)] += 1
            out.append(text[position : first.start()])
            out.append(replacement)
            position = last.end()
            index += size
        out.append(text[position:])
        return "".join(out)

    def _latin(self, text: str, found: Counter) -> str:
        def swap(match: re.Match) -> str:
            word = match.group()
            term = self.exact.get(word.lower())
            if term is None or term == word or _ARMENIAN.search(term):
                return word
            start = _SENTENCE_START.search(text[: match.start()])
            if word[0].isupper() and start and not term[0].isupper():
                term = term[0].upper() + term[1:]
            found[(word, term)] += 1
            return term

        return _LATIN.sub(swap, text)

    def fix(self, text: str) -> tuple[str, Counter]:
        found: Counter = Counter()
        return self._latin(self._armenian(text, found), found), found

    def fix_transcription(self, transcription: Transcription) -> tuple[Transcription, Counter]:
        found: Counter = Counter()
        segments = []
        for segment in transcription.segments:
            text, fixes = self.fix(segment.text)
            found.update(fixes)
            segments.append(replace(segment, text=text) if fixes else segment)
        return replace(transcription, segments=tuple(segments)), found
