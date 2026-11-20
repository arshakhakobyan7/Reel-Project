"""Groups typed in the dashboard are added to groups.toml at once (D-061)."""

import threading
import tomllib

import pytest

from reel.config.groups import GroupsFile, add_group, missing_group_message

BASE = '# my notes\n[groups."Python 12"]\naliases = ["Py12"]  # kept as it is\n'


def groups(path):
    return GroupsFile.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


def test_a_new_group_is_appended_and_the_rest_is_kept(tmp_path):
    path = tmp_path / "groups.toml"
    path.write_text(BASE, encoding="utf-8")
    assert add_group(path, "AI 2.6", note="added in the dashboard by anna\non 2026-11-20") == (
        "AI 2.6",
        True,
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith(BASE)
    assert '[groups."AI 2.6"]  # added in the dashboard by anna on 2026-11-20\n' in text
    found = groups(path)
    assert list(found.groups) == ["Python 12", "AI 2.6"]
    added = found.groups["AI 2.6"]
    assert (added.youtube_playlist_id, added.slack_channel, added.classroom_course_id) == ("",) * 3
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("same", ["Python 12", "python_12", "PYTHON-12", "py12"])
def test_an_existing_name_or_alias_is_not_added_again(tmp_path, same):
    path = tmp_path / "groups.toml"
    path.write_text(BASE, encoding="utf-8")
    assert add_group(path, same) == ("Python 12", False)
    assert path.read_text(encoding="utf-8") == BASE


def test_armenian_names_quotes_and_a_missing_file(tmp_path):
    path = tmp_path / "groups.toml"  # not made yet
    tricky = 'Ծրագրավորում "Ա" \\ 1'
    assert add_group(path, tricky) == (tricky, True)
    assert add_group(path, "Go 1") == ("Go 1", True)
    assert list(groups(path).groups) == [tricky, "Go 1"]


def test_a_file_without_a_last_line_break(tmp_path):
    path = tmp_path / "groups.toml"
    path.write_text('[groups."A 1"]', encoding="utf-8")
    add_group(path, "B 2")
    assert list(groups(path).groups) == ["A 1", "B 2"]


def test_a_broken_file_or_a_name_toml_cannot_hold_changes_nothing(tmp_path):
    broken = tmp_path / "broken.toml"
    broken.write_text("[groups.\n", encoding="utf-8")
    with pytest.raises(ValueError):
        add_group(broken, "X 1")
    assert broken.read_text(encoding="utf-8") == "[groups.\n"
    path = tmp_path / "groups.toml"
    path.write_text(BASE, encoding="utf-8")
    with pytest.raises(ValueError):
        add_group(path, "bad\x7fname")  # a control character
    assert path.read_text(encoding="utf-8") == BASE


def test_two_uploads_with_one_new_group_add_it_once(tmp_path):
    path = tmp_path / "groups.toml"
    path.write_text(BASE, encoding="utf-8")
    results, start = [], threading.Barrier(8)

    def add():
        start.wait()
        results.append(add_group(path, "AI 2.6"))

    threads = [threading.Thread(target=add) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [("AI 2.6", False)] * 7 + [("AI 2.6", True)]
    assert path.read_text(encoding="utf-8").count('[groups."AI 2.6"]') == 1


def test_the_hold_message_names_the_group():
    assert missing_group_message(None) == "Set the group of this recording, then release the job."
    assert missing_group_message("", True) == (
        "Set the group and lesson number of this recording, then release the job."
    )
    assert missing_group_message("AI 2.6") == (
        'The group "AI 2.6" is not in config/groups.toml: add it there or set another group, '
        "then release the job."
    )
    assert ", and set the lesson number," in missing_group_message("AI 2.6", True)
