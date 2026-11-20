"""The raw transcript on the cleaned timeline (T-406)."""

import pytest

from reel.cleaning.cutlist import Cut, Part, Timeline, build_cut_list
from reel.cleaning.remap import remap_segment, remap_transcript
from reel.config.schema import CutSettings
from reel.stt import Segment, Transcription, Word

PLAIN = CutSettings(padding_seconds=0, min_keep_seconds=0, min_remove_seconds=0)
# keep 0-10, remove 10-20, keep 20-40, remove 40-50, keep 50-60
CUT_LIST = build_cut_list(Timeline([Part(60.0)]), [Cut(10, 20, "a"), Cut(40, 50, "b")], PLAIN)


def test_segments_move_to_the_cleaned_timeline():
    segment = remap_segment(Segment(25, 30, "Հիմա for ցիկլը"), CUT_LIST)
    assert (segment.start, segment.end, segment.text) == (15, 20, "Հիմա for ցիկլը")


def test_a_segment_inside_a_cut_is_dropped():
    assert remap_segment(Segment(12, 18, "chatter"), CUT_LIST) is None


@pytest.mark.parametrize(
    ("start", "end", "kept"),
    [(8, 12, True), (6, 14, True), (9, 19, False)],  # kept share 2/4, 4/8, 1/10
)
def test_a_segment_over_a_cut_is_clipped_or_dropped_by_its_kept_share(start, end, kept):
    segment = remap_segment(Segment(start, end, "x"), CUT_LIST)
    if kept:
        assert segment.start == start and segment.end == 10  # clipped at the cut
    else:
        assert segment is None


def test_words_inside_a_cut_are_dropped_and_the_text_rebuilt():
    words = (
        Word(37.0, 38.0, " Այսօր"),
        Word(38.5, 39.5, " այսքանը"),
        Word(41.0, 42.0, " ա՜խ"),  # cut out
        Word(50.5, 51.0, " լավ"),
    )
    segment = remap_segment(Segment(37, 51, "Այսօր այսքանը ա՜խ լավ", words), CUT_LIST)
    assert segment.text == "Այսօր այսքանը լավ"
    assert [(w.start, w.end) for w in segment.words] == [(27, 28), (28.5, 29.5), (30.5, 31)]
    only_cut = Segment(41, 49, " ա՜խ", (Word(41, 42, " ա՜խ"),))
    assert remap_segment(only_cut, CUT_LIST) is None


def test_the_clean_transcript_is_ordered_and_inside_the_video():
    raw = Transcription(
        "hy",
        tuple(Segment(t, t + 3, f"line {t}") for t in range(0, 58, 2)),
        "test-model",
        {"seconds": 1.0},
    )
    clean = remap_transcript(raw, CUT_LIST)
    starts = [s.start for s in clean.segments]
    assert starts == sorted(starts)
    assert all(0 <= s.start <= s.end <= CUT_LIST.final_duration for s in clean.segments)
    assert len(clean.segments) < len(raw.segments)
    assert clean.details["remapped"]["final_duration"] == 40
    assert clean.details["seconds"] == 1.0 and clean.model == "test-model"
