"""Cut list and timeline mapping (T-404)."""

import pytest

from reel.cleaning.boundaries import Boundary, LessonBoundaries
from reel.cleaning.cutlist import (
    AFTER,
    BEFORE,
    Cut,
    CutList,
    CutListError,
    Part,
    Timeline,
    boundary_cuts,
    build_cut_list,
    load_cut_list,
    read_cut_list,
    save_cut_list,
    write_cut_list,
)
from reel.config.schema import CutSettings
from reel.db.enums import CutAction
from reel.db.models import Recording, validate_segments
from reel.queue.jobs import create_job

PLAIN = CutSettings(padding_seconds=0, min_keep_seconds=0, min_remove_seconds=0)
ONE = Timeline([Part(600.0)])  # 10 minutes, no frame alignment


def spans(cut_list: CutList) -> list[tuple[float, float, str]]:
    return [(s.start, s.end, s.action.value) for s in cut_list.segments]


def test_nothing_to_remove_keeps_everything():
    cut_list = build_cut_list(ONE, [], CutSettings())
    assert spans(cut_list) == [(0, 600, "keep")]
    assert cut_list.final_duration == 600 and cut_list.to_clean(123.4) == 123.4


def test_overlapping_and_touching_removals_are_merged_with_all_reasons():
    cuts = [Cut(100, 200, "silence"), Cut(150, 250, "nobody at the board"), Cut(250, 260, "x")]
    cut_list = build_cut_list(ONE, cuts, PLAIN)
    assert spans(cut_list) == [(0, 100, "keep"), (100, 260, "remove"), (260, 600, "keep")]
    assert cut_list.removed[0].reason == "silence; nobody at the board; x"


def test_padding_keeps_some_lesson_next_to_boundary_cuts_but_not_at_the_edges():
    boundaries = LessonBoundaries(Boundary(60, "greeting + topic"), Boundary(540, "closing"))
    cuts = boundary_cuts(boundaries, 600)
    assert [(c.start, c.end, c.reason) for c in cuts] == [(0, 60, BEFORE), (540, 600, AFTER)]
    cut_list = build_cut_list(ONE, cuts, CutSettings(padding_seconds=0.5))
    assert spans(cut_list) == [(0, 59.5, "remove"), (59.5, 540.5, "keep"), (540.5, 600, "remove")]


def test_already_shaped_removals_are_not_padded():
    """D-040: an empty stretch shortened to 5 s must stay 5 s."""
    cut_list = build_cut_list(ONE, [Cut(102.5, 197.5, "nobody")], CutSettings(padding_seconds=1))
    assert spans(cut_list)[1] == (102.5, 197.5, "remove")


def test_missing_boundaries_cut_nothing():
    assert boundary_cuts(LessonBoundaries(None, None), 600) == []


def test_short_removals_are_dropped_except_at_the_edges():
    settings = CutSettings(padding_seconds=0, min_remove_seconds=3, min_keep_seconds=0)
    cuts = [Cut(0, 1, "edge"), Cut(100, 102, "short"), Cut(200, 210, "long"), Cut(599, 600, "end")]
    assert spans(build_cut_list(ONE, cuts, settings)) == [
        (0, 1, "remove"),
        (1, 200, "keep"),
        (200, 210, "remove"),
        (210, 599, "keep"),
        (599, 600, "remove"),
    ]


def test_a_short_kept_piece_between_cuts_is_removed_too():
    settings = CutSettings(padding_seconds=0, min_remove_seconds=0, min_keep_seconds=2)
    cut_list = build_cut_list(ONE, [Cut(100, 200, "a"), Cut(201, 300, "b")], settings)
    assert spans(cut_list) == [(0, 100, "keep"), (100, 300, "remove"), (300, 600, "keep")]
    assert "too short to keep" in cut_list.removed[0].reason


def test_boundaries_are_put_on_video_frames():
    timeline = Timeline([Part(600.0, fps=24)])
    cut_list = build_cut_list(timeline, [Cut(100.01, 200.03, "x")], PLAIN)
    for segment in cut_list.segments:
        for value in (segment.start, segment.end):
            assert value * 24 == pytest.approx(round(value * 24), abs=1e-6)
    assert cut_list.removed[0].start == pytest.approx(100.0)


def test_parts_are_one_timeline_and_segments_never_cross_a_file():
    timeline = Timeline([Part(2862.0, 24, "IMG_0359"), Part(3162.0, 24, "IMG_0360")])
    assert timeline.duration == 6024 and timeline.locate(3000) == (1, 138)
    assert timeline.to_raw(1, 138) == 3000
    # nobody at the board from the end of part 1 into part 2
    cut_list = build_cut_list(timeline, [Cut(2800, 2900, "nobody")], PLAIN)
    assert [(s.start, s.end, s.action.value, s.part) for s in cut_list.segments] == [
        (0, 2800, "keep", 0),
        (2800, 2862, "remove", 0),
        (2862, 2900, "remove", 1),
        (2900, 6024, "keep", 1),
    ]
    whole = build_cut_list(timeline, [], PLAIN)
    assert [(s.end, s.part) for s in whole.kept] == [(2862, 0), (6024, 1)]
    assert whole.to_clean(2862) == 2862  # the join is seamless


CASES = [
    Cut(0, 30, BEFORE, pad=True),
    Cut(100, 150, "silence"),
    Cut(140, 160, "nobody"),
    Cut(300.3, 301.2, "short"),
    Cut(400, 410, "silence"),
    Cut(412, 420, "silence"),
    Cut(580, 600, AFTER, pad=True),
]


def test_mapping_is_monotonic_and_round_trips():
    timeline = Timeline([Part(250.0, 25), Part(350.0, 25)])
    cut_list = build_cut_list(timeline, CASES, CutSettings())
    assert cut_list.final_duration == pytest.approx(sum(s.duration for s in cut_list.kept))
    raw_times = [i / 10 for i in range(0, 6001)]
    clean_times = [cut_list.to_clean(t) for t in raw_times]
    assert all(b >= a for a, b in zip(clean_times, clean_times[1:], strict=False))
    assert clean_times[0] == 0 and clean_times[-1] == pytest.approx(cut_list.final_duration)
    for i in range(0, int(cut_list.final_duration * 10)):
        clean = i / 10
        assert cut_list.to_clean(cut_list.to_raw(clean)) == pytest.approx(clean, abs=1e-9)
    # a time inside a removed part maps to the cut
    removed = cut_list.removed[1]
    assert cut_list.to_clean((removed.start + removed.end) / 2) == pytest.approx(
        cut_list.to_clean(removed.end)
    )
    # segments cover the timeline without gaps and pass the database check
    assert cut_list.segments[0].start == 0 and cut_list.segments[-1].end == 600
    for a, b in zip(cut_list.segments, cut_list.segments[1:], strict=False):
        assert a.end == pytest.approx(b.start)
    validate_segments(cut_list.to_rows())


def test_removing_everything_is_an_error():
    with pytest.raises(CutListError, match="nothing would be kept"):
        build_cut_list(ONE, [Cut(0, 600, "everything")], PLAIN)


def test_json_file_round_trip(tmp_path):
    timeline = Timeline([Part(250.0, 25, "a.mov"), Part(350.0, None, "b.mov")])
    cut_list = build_cut_list(timeline, CASES, CutSettings())
    path = write_cut_list(tmp_path / "jobs" / "1" / "cutlist.json", cut_list)
    loaded = read_cut_list(path)
    assert loaded.to_dict() == cut_list.to_dict()
    assert loaded.to_raw(100) == cut_list.to_raw(100)
    assert not list(path.parent.glob("*.part"))


def test_database_versions(db):
    cut_list = build_cut_list(ONE, CASES, CutSettings())
    with db.transaction() as s:
        recording = Recording(source_path="x.mp4", size_bytes=1)
        s.add(recording)
        job_id = create_job(s, recording).id
        first = save_cut_list(s, job_id, cut_list)
    with db.transaction() as s:
        second = save_cut_list(s, job_id, build_cut_list(ONE, [], CutSettings()))
        assert (second.version, second.final_duration_seconds) == (2, 600)
    with db.transaction() as s:
        row = s.get(type(first), first.id)
        assert row.version == 1
        assert load_cut_list(row).to_dict() == cut_list.to_dict()
        assert row.job.latest_cut_list.version == 2


def test_segment_actions_are_the_database_enum():
    cut_list = build_cut_list(ONE, [Cut(10, 20, "x")], PLAIN)
    assert {s.action for s in cut_list.segments} == {CutAction.KEEP, CutAction.REMOVE}
