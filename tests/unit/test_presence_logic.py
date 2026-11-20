"""Empty-board stretches (T-403, D-040): the timeline logic, no FFmpeg."""

import pytest

from reel.cleaning.presence import BoardAnalysis, Sample, empty_stretches
from reel.config.schema import EmptyBoardSettings

SETTINGS = EmptyBoardSettings()  # nobody for more than 10 s -> keep 5 s


def timeline(*spans: tuple[float, float, float], step: float = 1.0) -> list[Sample]:
    """Samples every ``step`` seconds; each span is (from, to, score)."""
    samples = []
    for start, end, score in spans:
        t = start
        while t < end:
            samples.append(Sample(round(t, 3), score))
            t += step
    return samples


def test_a_long_empty_stretch_is_shortened_to_five_seconds():
    samples = timeline((0, 30, 0.9), (30, 90, 0.01), (90, 120, 0.9))
    empty, removals = empty_stretches(samples, SETTINGS, duration=120)
    assert [(e.start, e.end) for e in empty] == [(30, 89)]
    [cut] = removals
    assert (cut.start, cut.end) == (32.5, 86.5)  # half of the 5 s kept at each side
    assert (cut.start - 30) + (89 - cut.end) == SETTINGS.keep_seconds
    assert "nobody at the board" in cut.reason


@pytest.mark.parametrize("seconds", [5, 10])
def test_ten_seconds_or_less_is_kept(seconds):
    samples = timeline((0, 30, 0.9), (30, 30 + seconds + 1, 0.0), (31 + seconds, 60, 0.9))
    assert empty_stretches(samples, SETTINGS, duration=60) == ([], [])


def test_anybody_at_the_board_keeps_the_picture():
    samples = timeline((0, 600, 0.6))  # a weak but sure detection the whole time
    assert empty_stretches(samples, SETTINGS) == ([], [])


def test_a_person_seen_once_splits_the_stretch():
    samples = timeline((0, 20, 0.0), (20, 21, 0.8), (21, 29, 0.0), (29, 60, 0.9))
    empty, removals = empty_stretches(samples, SETTINGS)
    assert [(e.start, e.end) for e in empty] == [(0, 19)]  # 21..28 is only 7 s
    assert [(r.start, r.end) for r in removals] == [(2.5, 16.5)]


def test_a_gap_in_the_pictures_is_not_cut_across():
    """Unknown time (e.g. a damaged part of the file) never counts as empty."""
    samples = timeline((0, 8, 0.0)) + timeline((20, 28, 0.0))
    assert empty_stretches(samples, SETTINGS) == ([], [])


def test_nobody_until_the_end_reaches_the_end_of_the_video():
    samples = timeline((0, 50, 0.9), (50, 80, 0.0))  # last sample at 79 s
    empty, removals = empty_stretches(samples, SETTINGS, duration=80)
    assert [(e.start, e.end) for e in empty] == [(50, 80)]
    assert [(r.start, r.end) for r in removals] == [(52.5, 77.5)]


def test_the_score_and_the_lengths_come_from_config():
    samples = timeline((0, 30, 0.9), (30, 50, 0.5), (50, 80, 0.9))
    assert empty_stretches(samples, SETTINGS) == ([], [])
    strict = EmptyBoardSettings(person_score=0.7, min_empty_seconds=15, keep_seconds=2)
    _, [cut] = empty_stretches(samples, strict)
    assert (cut.start, cut.end) == (31, 48)


def test_samples_are_stored_compactly():
    analysis = BoardAnalysis((Sample(0.833, 0.91234),), (), (), "keyframes", 1.0)
    assert analysis.to_dict()["samples"] == [[0.833, 0.912]]
