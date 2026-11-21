"""The board framing (D-058): where the work happens, and the crop around it."""

import numpy as np
import pytest

from reel.cleaning.framing import (
    Box,
    Crop,
    FramingResult,
    WorkCollector,
    _box_blur,
    board_box,
    crop_rect,
    frame_lines,
    union,
)
from reel.config.schema import FramingSettings

W, H = 160, 90
SETTINGS = FramingSettings(window_seconds=10)  # ten pictures per still at one per second
WRITING = (50, 20, 110, 55)  # x0, y0, x1, y1 where the teacher writes


TEXTURE = np.random.default_rng(0).normal(0, 20, (H, W)).astype(np.float32)  # walls, desks


def room(rng: np.random.Generator) -> np.ndarray:
    """A grey room with fixed texture and a little camera noise, as a BGR picture."""
    base = np.linspace(90, 170, W, dtype=np.float32)[None, :].repeat(H, axis=0) + TEXTURE
    noisy = base + rng.normal(0, 2, (H, W))
    return np.repeat(np.clip(noisy, 0, 255).astype(np.uint8)[:, :, None], 3, axis=2)


def lesson(collector, *, windows=6, person=True, mask=True, shift_at=None, seed=1):
    """Writing grows in WRITING window by window; a person stands still for whole windows,
    first left, then right of the writing (outside it)."""
    rng = np.random.default_rng(seed)
    strokes = np.zeros((H, W), bool)
    x0, y0, x1, y1 = WRITING
    for window in range(windows):
        if window >= 1:  # one more line of writing per window
            row = y0 + 4 * window
            strokes[row : row + 2, x0 : x0 + (x1 - x0) * window // windows] = True
        if window == windows - 1:
            strokes[y1 - 2 : y1, x1 - 6 : x1] = True  # the last mark reaches the far corner
        spot = (5, 30, 25, 85) if window < windows // 2 else (130, 30, 150, 85)
        for _ in range(10):
            picture = room(rng)
            picture[strokes] = 30
            people = []
            if person:
                px0, py0, px1, py1 = spot
                picture[py0:py1, px0:px1] = 220
                if mask:
                    people = [spot]
            if shift_at is not None and window >= shift_at:
                picture = np.roll(picture, 25, axis=1)  # the camera was moved
            collector.add(picture, people)
    return collector.finish()


def test_the_box_is_where_the_writing_is_and_people_are_left_out():
    result = lesson(WorkCollector(SETTINGS, (W, H), 1.0))
    assert result.box is not None and result.note == "work found"
    box = result.box
    assert box.x0 * W == pytest.approx(WRITING[0], abs=3)
    assert box.x1 * W == pytest.approx(WRITING[2], abs=3)
    assert box.y0 * H == pytest.approx(WRITING[1] + 4, abs=3)
    assert box.y1 * H == pytest.approx(WRITING[3], abs=5)  # + the blur halo of the strokes
    assert result.windows == 6 and result.skipped == 0


def test_heads_near_the_camera_at_the_bottom_edge_are_left_out():
    """T-1002 lesson: a student's head at the bottom corner, missed by the detector, moved
    between the stills and pulled the box down to the bottom."""

    class Heads(WorkCollector):
        def add(self, frame, people=()):
            frame = frame.copy()
            if self.windows % 2:  # the head is there in every other window
                frame[80:90, 0:18] = 240
            super().add(frame, people)

    plain = lesson(WorkCollector(SETTINGS, (W, H), 1.0))
    with_heads = lesson(Heads(SETTINGS, (W, H), 1.0))
    assert with_heads.box == plain.box
    assert with_heads.details["dropped_at_bottom"] > 0


def test_a_person_standing_still_would_count_if_not_masked():
    unmasked = lesson(WorkCollector(SETTINGS, (W, H), 1.0), mask=False)
    assert unmasked.box.x0 * W < 10 and unmasked.box.x1 * W > 140  # the control case


def test_a_moved_camera_is_skipped():
    result = lesson(WorkCollector(SETTINGS, (W, H), 1.0), shift_at=3, person=False)
    assert result.skipped >= 1


def test_no_work_no_box():
    collector = WorkCollector(SETTINGS, (W, H), 1.0)
    rng = np.random.default_rng(3)
    for _ in range(60):
        collector.add(room(rng))
    result = collector.finish()
    assert result.box is None and result.note == "no writing or slide changes were seen"
    short = WorkCollector(SETTINGS, (W, H), 1.0)
    short.add(room(rng))
    assert short.finish().note == "too short to compare"


def test_the_box_blur_keeps_flat_pictures_flat_and_averages():
    flat = np.full((7, 9), 5.0, np.float32)
    assert np.allclose(_box_blur(flat, 2), 5.0)
    spike = np.zeros((9, 9), np.float32)
    spike[4, 4] = 9
    blurred = _box_blur(spike, 1)
    assert blurred.shape == (9, 9) and blurred[4, 4] == pytest.approx(1.0)
    assert blurred.sum() == pytest.approx(9.0)


def test_the_result_is_written_for_the_summary():
    data = lesson(WorkCollector(SETTINGS, (W, H), 1.0)).to_dict()
    assert data["note"] == "work found" and len(data["box"]) == 4
    assert data["analysis_size"] == [W, H] and data["work_share"] > 0


# --- the crop ------------------------------------------------------------------------------


def test_the_crop_adds_a_margin_keeps_the_shape_and_limits_the_zoom():
    crop, note = crop_rect(Box(0.3, 0.2, 0.7, 0.6), 3840, 2160, FramingSettings())
    assert crop == Crop(960, 324, 1920, 1080)  # 2x at most: 4K becomes 1080p, full detail
    assert note == "zoom 2.00x"
    wider, _ = crop_rect(Box(0.1, 0.2, 0.8, 0.6), 3840, 2160, FramingSettings(margin=0.06))
    margin = 0.06 * 0.7 * 3840
    assert wider.width == pytest.approx(0.7 * 3840 + 2 * margin, abs=4)
    assert wider.width / wider.height == pytest.approx(16 / 9, abs=0.01)
    assert wider.x == pytest.approx(0.1 * 3840 - margin, abs=4)


def test_a_tall_box_is_widened_and_the_crop_stays_inside_the_picture():
    crop, _ = crop_rect(Box(0.0, 0.0, 0.25, 0.7), 1920, 1080, FramingSettings(max_zoom=4))
    assert crop.x == 0 and crop.y == 0  # pushed back inside
    assert crop.width / crop.height == pytest.approx(16 / 9, abs=0.01)
    assert crop.height >= 0.7 * 1080
    assert all(value % 2 == 0 for value in (crop.x, crop.y, crop.width, crop.height))


@pytest.mark.parametrize(
    ("box", "note"),
    [
        (None, "nothing to frame"),
        (Box(0.02, 0.05, 0.98, 0.9), "the work fills the picture"),
        (Box(0.0, 0.3, 1.0, 0.5), "the work fills the picture"),  # a full-width strip
    ],
)
def test_no_crop_when_it_is_not_worth_it(box, note):
    assert crop_rect(box, 3840, 2160, FramingSettings()) == (None, note)


def test_the_margin_is_at_least_a_share_of_the_picture():
    tiny = FramingSettings(margin=0.0, min_margin=0.05, max_zoom=4)
    crop, _ = crop_rect(Box(0.4, 0.4, 0.6, 0.6), 1000, 1000, tiny)
    assert crop.width >= 0.3 * 1000 - 2  # 0.2 of the box + 2 x 0.05 of the picture


def test_small_traces_on_their_own_are_left_out():
    """A laptop screen on a table changes now and then: a small group far from the board."""

    class Laptop(WorkCollector):
        def add(self, frame, people=()):
            frame = frame.copy()
            if self.windows % 2:
                spot = frame[70:72, 150:152].astype(np.int16) + 80  # a small screen
                frame[70:72, 150:152] = np.clip(spot, 0, 255).astype(np.uint8)
            super().add(frame, people)

    plain = lesson(WorkCollector(SETTINGS, (W, H), 1.0))
    with_laptop = lesson(Laptop(SETTINGS, (W, H), 1.0))
    assert with_laptop.box == plain.box
    found, kept = with_laptop.details["groups"]
    assert found > kept >= 1


def test_the_union_of_boxes():
    assert union([None, None]) is None
    assert union([Box(0.2, 0.3, 0.5, 0.6), None, Box(0.1, 0.4, 0.4, 0.7)]) == Box(
        0.1, 0.3, 0.5, 0.7
    )


# --- the whole board (D-058, the user's correction) -----------------------------------------


def classroom(board_value: float, wall_value: float) -> np.ndarray:
    """A board (x 20-140, y 15-75) with a light rail at x 79-81 between two panels, a dark
    pole from top to bottom at x 8-12 and dark legs under the board."""
    picture = np.full((H, W), wall_value, np.float32)
    picture[15:75, 20:140] = board_value
    picture[15:75, 79:82] = (board_value + wall_value) / 2  # the rail
    picture[:, 8:13] = 20.0  # a pole
    picture[75:H, 40:44] = 20.0  # legs
    picture[75:H, 110:114] = 20.0
    return picture


def test_the_board_is_found_around_the_work_across_rails_and_not_along_poles():
    background = classroom(50.0, 200.0)
    work = np.zeros((H, W), bool)
    work[30:50:4, 30:70] = True  # lines of writing, only on the left panel
    found, note = board_box(background, work, (30, 30, 70, 50))
    assert found == (20, 15, 140, 75), note
    assert note.startswith("board surface")


def test_a_white_board_on_a_white_wall_cannot_be_told_apart():
    background = classroom(200.0, 205.0)
    work = np.zeros((H, W), bool)
    work[30:50:4, 30:70] = True
    assert board_box(background, work, (30, 30, 70, 50)) == (
        None,
        "the surface cannot be told from the room",
    )


def test_the_frame_keeps_the_board_and_the_work():
    result = FramingResult(Box(0.3, 0.3, 0.5, 0.5), "work found", board=Box(0.1, 0.2, 0.9, 0.8))
    assert result.frame == Box(0.1, 0.2, 0.9, 0.8)
    beyond = FramingResult(Box(0.05, 0.3, 0.5, 0.95), "work found", board=Box(0.1, 0.2, 0.9, 0.8))
    assert beyond.frame == Box(0.05, 0.2, 0.9, 0.95)  # writing on the wall next to the board
    assert FramingResult(Box(0.3, 0.3, 0.5, 0.5), "work found").frame == Box(0.3, 0.3, 0.5, 0.5)
    assert FramingResult(None, "no writing").frame is None


def test_the_collector_finds_the_board_of_a_lesson():
    """The synthetic lesson, drawn on a dark board in a light room."""

    class OnBoard(WorkCollector):
        def add(self, frame, people=()):
            room_picture = classroom(60.0, 190.0)
            picture = np.repeat(room_picture.astype(np.uint8)[:, :, None], 3, axis=2)
            strokes = frame[:, :, 0] == 30  # the writing of the synthetic lesson
            picture[strokes] = 235  # chalk
            super().add(picture, people)

    result = lesson(OnBoard(SETTINGS, (W, H), 1.0), person=False)
    assert result.board is not None
    board = result.board
    assert (round(board.x0 * W), round(board.y0 * H)) == (20, 15)
    assert (round(board.x1 * W), round(board.y1 * H)) == (140, 75)


# --- T-1011: a white board on a light wall, legs under a desk --------------------------------


def test_legs_under_a_desk_are_left_out_even_when_they_are_much_of_the_work():
    """A teacher behind a desk: the legs show under it, outside the detector's box, and move.
    In a lesson with little writing they were 44 % of the work and pulled the box down."""

    class Legs(WorkCollector):
        def add(self, frame, people=()):
            frame = frame.copy()
            if self.windows % 2:
                frame[80:90, 40:120] = 240
            super().add(frame, people)

    plain = lesson(WorkCollector(SETTINGS, (W, H), 1.0))
    with_legs = lesson(Legs(SETTINGS, (W, H), 1.0))
    assert with_legs.box == plain.box
    assert with_legs.details["dropped_at_bottom"] > 0.3


def test_writing_that_reaches_the_bottom_edge_is_kept():
    collector = WorkCollector(SETTINGS, (W, H), 1.0)
    work = np.zeros((H, W), np.uint16)
    work[30:72:4, 50:110] = 1  # lines of writing ...
    work[31:72:4, 50:110] = 1
    work[74:H, 50:110] = 1  # ... down to a large mark at the bottom edge, close below them
    collector.work, collector.windows = work, 2
    result = collector.finish()
    assert result.box.y1 == 1.0 and "dropped_at_bottom" not in result.details


def light_classroom(wall_value: float = 168.0) -> np.ndarray:
    """A white board (x 20-140, y 15-75) on a light wall, with a darker frame (x 19 and 140,
    y 14 and 75), rails between its panels at x 60 and 100, and a dark chair at its left."""
    picture = np.full((H, W), wall_value, np.float32)
    picture[15:75, 20:140] = 175.0
    picture[[14, 75], 19:141] = 150.0
    picture[14:76, [19, 140]] = 150.0
    picture[15:75, [60, 100]] = 150.0  # rails
    picture[70:H, 2:15] = 60.0  # a chair; its top edge is near the board's bottom line
    picture[73:H, 2:15] = 40.0
    return picture


def test_a_white_board_on_a_light_wall_is_found_by_its_frame():
    work = np.zeros((H, W), bool)
    work[30:50:4, 65:95] = True  # writing on the middle panel only
    found, note = board_box(light_classroom(), work, (65, 30, 95, 50))
    assert note.startswith("board frame lines"), note
    x0, y0, x1, y1 = found
    # the outer frame, not the rails, and not on along the chair; edges are found to +-2 px
    assert abs(x0 - 19) <= 2 and abs(x1 - 141) <= 2
    assert abs(y0 - 14) <= 2 and abs(y1 - 76) <= 2


def test_frame_lines_need_a_line_above_and_below_the_work():
    picture = light_classroom()
    picture[15:H, 20:140] = 175.0  # the board goes on below the picture: no bottom line
    picture[14:H, [19, 140]] = 150.0
    assert frame_lines(picture, (65, 30, 95, 50)) is None


def test_a_board_wider_than_the_picture_runs_to_its_edges():
    picture = np.full((H, W), 168.0, np.float32)
    picture[15:75, :] = 175.0
    picture[[14, 75], :] = 150.0
    found, note = board_box(picture, np.zeros((H, W), bool), (65, 30, 95, 50))
    assert note.startswith("board frame lines")
    assert (found[0], found[2]) == (0, W)
