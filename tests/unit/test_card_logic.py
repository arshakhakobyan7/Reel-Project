"""The shared Reel card without FFmpeg: background, fonts, fitting, literal text (T-1018)."""

import pytest

from reel.media import card
from reel.media.thumbnail import WIDTH_SAFETY, MissingGlyphs, ThumbnailError, _advances, _font


def pixel(x, y):
    data = card.background()
    offset = (y * card.WIDTH + x) * 3
    return tuple(data[offset : offset + 3])


def close(a, b, tolerance=3):
    return all(abs(p - q) <= tolerance for p, q in zip(a, b, strict=True))


def width(font, text, size):
    tables, mappings = _font(font)
    widths = _advances(tables, mappings, size)
    return max(sum(widths[c] for c in line) for line in text.split("\n"))


def test_background_runs_from_the_logo_colour_to_deep_violet():
    assert len(card.background()) == 1280 * 720 * 3
    assert close(pixel(0, 0), card.LOGO_COLOUR)
    assert close(pixel(1279, 719), card.DEEP_VIOLET)
    assert card.background() is card.background()  # made once per process


def test_the_round_shapes_lighten_the_background():
    ring = pixel(1090, 630)  # on the ring: 250 px below its centre
    beside = pixel(1090, 660)  # 30 px further out: the gradient alone
    assert ring[1] > beside[1] + 10  # white mixed in raises green most (the gradient has ~10)
    disc = pixel(1190, 110)  # the centre of the upper disc
    assert disc[1] > pixel(1000, 110)[1] + 10


def test_latin_text_uses_outfit_and_armenian_falls_back_to_dejavu():
    assert card.font_for("Python 12", card.CARD_FONT) == card.CARD_FONT
    assert card.font_for("Պիթոն 12", card.CARD_FONT) == card.FALLBACK_FONT
    with pytest.raises(MissingGlyphs):
        card.font_for("数据 12", card.CARD_FONT)


def test_short_group_names_get_the_largest_size():
    font, text, size = card.fit_group("  test  ")
    assert (font, text, size) == (card.CARD_FONT, "test", card.GROUP_SIZES[0])


def test_longer_names_shrink_then_wrap_into_two_lines_at_most():
    sizes = []
    for name in ("Data Science Bootcamp", "Python Advanced Evening 12", "x " * 80):
        font, text, size = card.fit_group(name)
        assert text.count("\n") <= 1
        assert width(font, text, size) <= card.TEXT_WIDTH - WIDTH_SAFETY
        sizes.append(size)
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[-1] == card.GROUP_SIZES[-1]
    assert card.fit_group("x " * 80)[1].endswith("…")


def test_an_empty_group_name_is_refused():
    with pytest.raises(ThumbnailError):
        card.fit_group(" \n ")


def test_texts_are_written_literally_and_drawn_from_files(tmp_path):
    group = "A:B 'C' %{pts} \\ 100%"
    graph = card.card_graph(tmp_path, group, "Lecture 3")
    assert (tmp_path / "group.txt").read_text(encoding="utf-8") == group
    assert (tmp_path / "lesson.txt").read_text(encoding="utf-8") == "Lecture 3"
    assert (tmp_path / "brand.txt").read_text(encoding="utf-8") == "Reel"
    assert graph.count("drawtext=") == 3
    assert graph.count("expansion=none") == 3
    assert "%{pts}" not in graph


def test_no_lesson_line_when_the_label_is_empty(tmp_path):
    assert card.card_graph(tmp_path, "test", "").count("drawtext=") == 2
