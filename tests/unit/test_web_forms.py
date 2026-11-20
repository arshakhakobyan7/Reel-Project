"""Typed group names and lesson numbers (reel.web.forms)."""

import pytest

from reel.config.groups import GroupConfig, GroupsFile
from reel.web.forms import parse_group, parse_lesson

GROUPS = GroupsFile(
    groups={"Python 12": GroupConfig(aliases=("Պայթոն 12",)), "Java 3": GroupConfig()}
)


@pytest.mark.parametrize(
    ("typed", "name"),
    [
        ("Python 12", "Python 12"),
        ("  python_12 ", "Python 12"),
        ("պայթոն 12", "Python 12"),  # an alias, Armenian lower case
        ("Go  1", "Go 1"),  # unknown: kept, with spaces tidied
        ("C++	2", "C++ 2"),
    ],
)
def test_group_names(typed, name):
    assert parse_group(typed, GROUPS) == name


@pytest.mark.parametrize("typed", ["", "   ", "x" * 101])
def test_wrong_group_names(typed):
    with pytest.raises(ValueError):
        parse_group(typed, GROUPS)


@pytest.mark.parametrize(
    ("typed", "number"), [("", None), (None, None), (" 14 ", 14), ("0", 0), (7, 7)]
)
def test_lesson_numbers(typed, number):
    assert parse_lesson(typed) == number


@pytest.mark.parametrize("typed", ["x", "-2", "1.5", "ձ", "123456", True, "1 2"])
def test_wrong_lesson_numbers(typed):
    with pytest.raises(ValueError, match="Դասի համարը"):
        parse_lesson(typed)
