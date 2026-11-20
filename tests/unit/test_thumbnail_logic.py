"""Thumbnail wrapping, literal filter paths and cmap parsing without FFmpeg (T-503)."""

import struct

import pytest

from reel.media import thumbnail
from reel.media.ffmpeg import FfmpegCancelled
from reel.media.thumbnail import (
    DEFAULT_FONT,
    REGULAR_FONT,
    MissingGlyphs,
    ThumbnailError,
    choose_start,
    escape_filter_value,
    make_thumbnail,
    missing_glyphs,
    read_cmap,
    wrap_title,
)


def font_file(tmp_path, subtables):
    offset = 4 + 8 * len(subtables)
    records, bodies = [], []
    for encoding, body in subtables:
        records.append(struct.pack(">HHI", 3, encoding, offset))
        bodies.append(body)
        offset += len(body)
    cmap = struct.pack(">HH", 0, len(records)) + b"".join(records + bodies)
    sfnt = struct.pack(">IHHHH", 0x10000, 1, 0, 0, 0)
    sfnt += struct.pack(">4sIII", b"cmap", 0, 28, len(cmap)) + cmap
    path = tmp_path / "synthetic.ttf"
    path.write_bytes(sfnt)
    return path


def format4(*, ranged=False, delta=-62):
    glyphs = struct.pack(">HHH", 3, 0, 65534) if ranged else b""
    return (
        struct.pack(">7H", 4, 32 + len(glyphs), 0, 4, 4, 1, 0)
        + struct.pack(">3H", 67 if ranged else 65, 65535, 0)
        + struct.pack(">2H", 65, 65535)
        + struct.pack(">2h", 2 if ranged else delta, 1)
        + struct.pack(">2H", 4 if ranged else 0, 0)
        + glyphs
    )


def format12(groups):
    return struct.pack(">HHIII", 12, 0, 16 + 12 * len(groups), 0, len(groups)) + b"".join(
        struct.pack(">III", *group) for group in groups
    )


def test_format4_delta_and_missing_glyph_zero(tmp_path):
    path = font_file(tmp_path, [(1, format4())])
    assert read_cmap(path) == {65: 3}
    assert missing_glyphs(path, "AAZ\n\tZ") == ("Z",)
    path = font_file(tmp_path, [(1, format4(delta=-65))])
    assert read_cmap(path) == {}


def test_format4_range_offsets_delta_and_zero_modulo(tmp_path):
    path = font_file(tmp_path, [(1, format4(ranged=True))])
    assert read_cmap(path) == {65: 5}  # B has glyph zero; C wraps to zero after delta.


def test_format12_supplementary_characters_and_glyph_zero(tmp_path):
    path = font_file(tmp_path, [(10, format12([(65, 65, 0), (0x1F600, 0x1F601, 10)]))])
    assert read_cmap(path) == {0x1F600: 10, 0x1F601: 11}


def test_unicode_format12_takes_precedence_over_format4(tmp_path):
    path = font_file(tmp_path, [(1, format4()), (10, format12([(66, 66, 7)]))])
    assert read_cmap(path) == {66: 7}


@pytest.mark.parametrize("raw", [b"", b"not a font", b"\x00\x01\x00\x00" + b"\xff" * 30])
def test_corrupt_font_is_a_clear_error(tmp_path, raw):
    path = tmp_path / "bad.ttf"
    path.write_bytes(raw)
    with pytest.raises(ThumbnailError, match="Unicode cmap"):
        read_cmap(path)


def test_corrupt_subtable_bounds_and_unsupported_format_are_rejected(tmp_path):
    path = font_file(tmp_path, [(1, format4()[:-2])])
    with pytest.raises(ThumbnailError):
        read_cmap(path)
    path = font_file(tmp_path, [(1, struct.pack(">HHH", 6, 6, 0))])
    with pytest.raises(ThumbnailError):
        read_cmap(path)


@pytest.mark.parametrize("font", [DEFAULT_FONT, REGULAR_FONT])
def test_bundled_fonts_cover_armenian_latin_and_punctuation(font):
    armenian = "".join(chr(cp) for cp in range(0x531, 0x557))
    armenian += "".join(chr(cp) for cp in range(0x561, 0x588)) + "։՝՞"
    latin = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 :',\\%[];"
    assert missing_glyphs(font, armenian + latin) == ()


def test_missing_cjk_and_emoji_are_reported_by_character():
    assert missing_glyphs(DEFAULT_FONT, "Հայերեն 漢 🦄 漢") == ("漢", "🦄")
    error = MissingGlyphs(("漢", "🦄"))
    assert "U+6F22" in str(error) and "U+1F984" in str(error) and "漢" in str(error)


def test_wrapping_counts_characters_not_utf8_bytes_and_limits_to_three_lines():
    text = "Աբգդ եզի էթժ իլխ ծկհ ձղճ մյն շոչ պջռ սվտ րցւ փքօֆ"
    wrapped = wrap_title(text, width=12)
    assert len(wrapped.splitlines()) == 3 and all(len(line) <= 12 for line in wrapped.splitlines())
    assert wrapped.endswith("…") and wrapped.startswith("Աբգդ եզի էթժ")
    assert wrap_title("Ա" * 100, width=10).splitlines() == ["Ա" * 10, "Ա" * 10, "Ա" * 9 + "…"]
    assert wrap_title("  one\n two\tthree  ", width=20) == "one two three"


@pytest.mark.parametrize("width,lines", [(0, 3), (10, 0), (-1, 3), (True, 3)])
def test_invalid_wrapping_bounds_are_rejected(width, lines):
    with pytest.raises(ValueError):
        wrap_title("title", width=width, max_lines=lines)


@pytest.mark.parametrize(
    "raw,escaped",
    [
        ("a:b", r"a\\:b"),
        ("a,b", r"a\,b"),
        ("[a];b", r"\[a\]\;b"),
        ("O'Brien", r"O\\\'Brien"),
        ("\\", r"\\\\"),
        ("100%", "100%"),
    ],
)
def test_filter_paths_are_escaped_for_both_parser_levels(raw, escaped):
    assert escape_filter_value(raw) == escaped


@pytest.mark.parametrize(
    "duration,skip,fps,expected",
    [
        (600, 180, 25, 180),
        (10, 180, 25, 5),
        (10, 10, 25, 5),
        (1, 0, 25, 0.5),
        (0.04, 180, 25, 0),
        (10, 2, 25, 2),
    ],
)
def test_start_selection_for_long_short_and_single_frame_videos(duration, skip, fps, expected):
    assert choose_start(duration, skip, fps=fps) == pytest.approx(expected)


@pytest.mark.parametrize("duration,skip", [(0, 180), (-1, 180), (10, -1), (float("nan"), 1)])
def test_bad_duration_or_skip_is_rejected(duration, skip):
    with pytest.raises(ValueError):
        choose_start(duration, skip)


def test_missing_glyph_is_refused_before_any_media_command(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("missing glyph must be refused before media processing")

    monkeypatch.setattr(thumbnail, "probe", unexpected)
    monkeypatch.setattr(thumbnail, "run_ffmpeg", unexpected)
    with pytest.raises(MissingGlyphs, match="U\\+6F22"):
        make_thumbnail("ffmpeg", "ffprobe", tmp_path / "video.mp4", "漢", tmp_path / "thumb.jpg")
    assert list(tmp_path.iterdir()) == []


def test_already_cancelled_call_never_starts_media_processing(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("already-cancelled call must not probe or run FFmpeg")

    monkeypatch.setattr(thumbnail, "probe", unexpected)
    with pytest.raises(FfmpegCancelled):
        make_thumbnail(
            "ffmpeg",
            "ffprobe",
            "video.mp4",
            "Դաս",
            tmp_path / "thumb.jpg",
            on_cancel=lambda cancel: cancel(),
        )


REVIEW_TITLE = "Սեփական ֆունկցիաներ, callable օբյեկտներ և hash աղյուսակներ Python-ում"


def assert_measured_fit(wrapped, *, size=48, font=DEFAULT_FONT, lines=3):
    assert len(wrapped.splitlines()) <= lines
    for line in wrapped.splitlines():
        assert thumbnail.measure_text(line, font=font, font_size=size) <= (
            thumbnail.TEXT_WIDTH - thumbnail.WIDTH_SAFETY
        )


def test_measured_review_title_keeps_all_words_without_ellipsis():
    wrapped = thumbnail.wrap_measured(REVIEW_TITLE)
    assert " ".join(wrapped.splitlines()) == REVIEW_TITLE
    assert "…" not in wrapped
    assert_measured_fit(wrapped)
    assert max(thumbnail.measure_text(line) for line in wrapped.splitlines()) > 900


@pytest.mark.parametrize("letter", ["W", "Մ"])
def test_measured_wide_word_is_split_without_overflow(letter):
    title = letter * 60
    wrapped = thumbnail.wrap_measured(title)
    assert wrapped.replace("\n", "") == title
    assert_measured_fit(wrapped)


def test_measured_long_title_and_subtitle_have_fitting_ellipses():
    wrapped = thumbnail.wrap_measured(REVIEW_TITLE * 10)
    assert wrapped.endswith("…")
    assert_measured_fit(wrapped)
    subtitle = thumbnail.wrap_measured(
        REVIEW_TITLE * 10, font=REGULAR_FONT, font_size=28, max_lines=1
    )
    assert subtitle.endswith("…")
    assert_measured_fit(subtitle, size=28, font=REGULAR_FONT, lines=1)


def test_measured_advances_distinguish_narrow_and_wide_letters():
    assert thumbnail.measure_text("WWW") > 3 * thumbnail.measure_text("iii")
    assert thumbnail.measure_text("Wi") == pytest.approx(
        thumbnail.measure_text("W") + thumbnail.measure_text("i")
    )
