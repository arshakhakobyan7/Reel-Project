"""Groups mapping, prompt files and secrets (T-003)."""

import tomllib

import pytest
from pydantic import ValidationError

from reel.config import GroupsFile, PromptError, PromptLibrary, Secrets, Settings, required_secrets


def groups(text: str) -> GroupsFile:
    return GroupsFile.model_validate(tomllib.loads(text))


# --- groups --------------------------------------------------------------------------------


def test_group_lookup_ignores_case_separators_and_uses_aliases():
    mapping = groups('[groups."Python 12"]\naliases = ["Py12"]\n[groups."Դիզայն 3"]\n')
    for folder in ("Python 12", "python 12", "PYTHON_12", "python-12", " py12 "):
        assert mapping.find(folder)[0] == "Python 12"
    assert mapping.find("դիզայն 3")[0] == "Դիզայն 3"
    assert mapping.find("Java 1") is None


def test_name_used_by_two_groups_is_rejected():
    with pytest.raises(ValidationError, match="matches both"):
        groups('[groups.A]\naliases = ["x"]\n[groups.B]\naliases = ["X"]\n')


def test_numeric_ids_are_kept_as_text():
    mapping = groups("[groups.A]\nclassroom_course_id = 123456789\n")
    assert mapping.groups["A"].classroom_course_id == "123456789"


def test_unknown_group_setting_is_rejected():
    with pytest.raises(ValidationError, match="Extra inputs"):
        groups('[groups.A]\nplaylist = "PL1"\n')


# --- prompts -------------------------------------------------------------------------------


def test_prompt_is_filled_in_and_reread_on_every_use(tmp_path):
    library = PromptLibrary(tmp_path)
    (tmp_path / "chapters.txt").write_text('Lesson $lesson: {"json": true}', encoding="utf-8")
    assert library.render("chapters", lesson="Դաս 14") == 'Lesson Դաս 14: {"json": true}'
    (tmp_path / "chapters.txt").write_text("Changed ${lesson}", encoding="utf-8")
    assert library.render("chapters", lesson=1) == "Changed 1"


def test_prompt_errors_are_clear(tmp_path):
    library = PromptLibrary(tmp_path)
    (tmp_path / "chapters.txt").write_text("Lesson $lesson", encoding="utf-8")
    with pytest.raises(PromptError, match=r"uses \$lesson"):
        library.render("chapters")
    with pytest.raises(PromptError, match="not found"):
        library.render("missing")
    with pytest.raises(PromptError, match="invalid prompt name"):
        library.render("../secrets")


# --- secrets -------------------------------------------------------------------------------


def test_required_secrets_follow_the_enabled_features():
    assert required_secrets(Settings()) == {}
    live = Settings.model_validate(
        {
            "slack": {"mode": "live"},
            "youtube": {"mode": "live"},
            "consent": {"provider": "http", "http_base_url": "https://core.example"},
            "library": {"provider": "http", "http_base_url": "https://library.example"},
        }
    )
    assert set(required_secrets(live)) == {
        "SLACK_BOT_TOKEN",
        "GOOGLE_CLIENT_SECRETS_FILE",
        "CORE_ENGINE_API_TOKEN",
        "LIBRARY_API_TOKEN",
    }


def test_secrets_reject_unknown_names_and_ignore_empty_values():
    secrets = Secrets.from_env({"SLACK_BOT_TOKEN": "  ", "LIBRARY_API_TOKEN": "abc"})
    assert secrets.get("SLACK_BOT_TOKEN") is None
    assert secrets.names_set() == ["LIBRARY_API_TOKEN"]
    with pytest.raises(KeyError):
        secrets.get("OTHER_TOKEN")
