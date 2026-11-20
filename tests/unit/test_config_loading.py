"""Loading, validation, environment overrides and reloading of the configuration (T-003)."""

import tomllib
from pathlib import Path

import pytest
from pydantic import BaseModel

from reel.config import ConfigError, ConfigProvider, Settings, load_config

EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "reel.example.toml"
SECRET_WORDS = ("token", "secret", "password", "passwd", "api_key", "apikey", "credential")


def load(project: Path, env: dict[str, str] | None = None):
    return load_config(project / "config" / "reel.toml", environ=env or {})


def all_setting_names(model: type[BaseModel], prefix: str = ""):
    for name, field in model.model_fields.items():
        yield prefix + name
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel):
            yield from all_setting_names(field.annotation, f"{prefix}{name}.")


# --- the example configuration ----------------------------------------------------------


def test_example_config_loads_with_paths_relative_to_the_project(project):
    cfg = load(project)
    root = project.resolve()
    assert cfg.settings.stt.language == "hy"
    assert cfg.paths.base_dir == root
    assert cfg.paths.data_dir == root / "data"
    assert cfg.paths.watch_folders == (root / "inbox",)
    assert cfg.paths.ffmpeg == root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    assert cfg.paths.groups_file == root / "config" / "groups.toml"
    assert cfg.groups.find("python 12")[0] == "Python 12"
    assert "բարև ձեզ" in cfg.settings.cleaning.boundaries.greeting_phrases


def test_example_config_documents_exactly_the_defaults():
    raw = tomllib.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert Settings.model_validate(raw) == Settings()


def test_short_config_uses_defaults(project, write_config):
    write_config("[workers]\ncount = 4\n")
    cfg = load(project)
    assert cfg.settings.workers.count == 4
    assert cfg.settings.stt.model == Settings().stt.model


# --- validation messages ------------------------------------------------------------------


def test_invalid_value_names_the_setting(project, write_config):
    write_config("[workers]\ncount = 0\n")
    with pytest.raises(ConfigError) as exc:
        load(project)
    message = str(exc.value)
    assert "workers.count" in message
    assert "greater than or equal to 1" in message
    assert "(got 0)" in message


def test_unknown_setting_is_rejected_without_echoing_its_value(project, write_config):
    write_config('[slack]\nbot_token = "xoxb-should-not-be-printed"\n')
    with pytest.raises(ConfigError) as exc:
        load(project)
    message = str(exc.value)
    assert "slack.bot_token" in message
    assert "unknown setting" in message
    assert "xoxb-should-not-be-printed" not in message


def test_toml_syntax_error_shows_the_line(project, write_config):
    write_config("[workers]\ncount = \n")
    with pytest.raises(ConfigError, match=r"TOML syntax error.*line 2"):
        load(project)


def test_missing_config_file_explains_what_to_do(tmp_path):
    with pytest.raises(ConfigError, match="reel config init"):
        load_config(tmp_path / "config" / "reel.toml", environ={})


def test_missing_groups_file(project):
    (project / "config" / "groups.toml").unlink()
    with pytest.raises(ConfigError, match="groups file not found"):
        load(project)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[workers]\ncount = 1\nheavy_stage_limit = 2\n", "heavy_stage_limit cannot be larger"),
        ('[consent]\nprovider = "http"\n', "http_base_url"),
        ('[library]\nprovider = "http"\nhttp_base_url = "ftp://x"\n', "http_base_url"),
        ('[metadata]\ntitle_template = "{group} {teacher}"\n', "unknown placeholder {teacher}"),
        ('[slack]\nmessage_template = "{title} {"\n', "invalid template"),
        ("[ingest]\nlesson_number_patterns = ['lecture (\\d+)-(\\d+)']\n", "exactly one (group)"),
        ("[ingest]\nlesson_number_patterns = ['lecture (']\n", "invalid regular expression"),
        ("[ingest]\ndate_patterns = ['(\\d{4})']\n", "named groups"),
        ('[ingest]\nextensions = ["mp4"]\n', "leading dot"),
        ('[schedule]\ntimezone = "Mars/Olympus"\n', "unknown time zone"),
        ("[cleaning.silence]\nmin_silence_seconds = 2.0\nkeep_seconds = 1.0\n", "keep_seconds"),
        ("[cleaning.empty_board]\nmin_empty_seconds = 10.0\nkeep_seconds = 10.0\n", "smaller"),
        ('[cleaning.boundaries]\nclosing_phrases = [" "]\n', "non-empty phrases"),
        ('[notifications]\nmode = "live"\n', "slack_channel is required"),
        ('[render]\nvideo_encoder = "h265"\n', "render.video_encoder"),
        ('[youtube]\nmode = "public"\n', "youtube.mode"),
    ],
)
def test_validation_rules(project, write_config, text, expected):
    write_config(text)
    with pytest.raises(ConfigError) as exc:
        load(project)
    assert expected in str(exc.value)


def test_schema_has_no_secret_fields():
    names = list(all_setting_names(Settings))
    assert len(names) > 80, "the walk must reach the nested sections"
    assert [n for n in names if any(word in n.lower() for word in SECRET_WORDS)] == []


def test_youtube_privacy_is_not_configurable():
    assert [n for n in all_setting_names(Settings) if "privacy" in n] == []


# --- environment overrides and .env ---------------------------------------------------------


def test_environment_overrides_settings(project):
    cfg = load(
        project,
        {
            "REEL__WORKERS__COUNT": "4",
            "REEL__INGEST__WATCH_FOLDERS": '["a", "b"]',
            "REEL__CLEANING__SILENCE__NOISE_DB": "-40",
            "REEL__YOUTUBE__CATEGORY_ID": "27",
            "REEL__WEB__BASE_URL": "http://reel.local:9000",
        },
    )
    root = project.resolve()
    assert cfg.settings.workers.count == 4
    assert cfg.paths.watch_folders == (root / "a", root / "b")
    assert cfg.settings.cleaning.silence.noise_db == -40
    assert cfg.settings.youtube.category_id == "27"
    assert cfg.settings.web.base_url == "http://reel.local:9000"
    assert cfg.overrides["workers.count"] == "REEL__WORKERS__COUNT"


def test_invalid_environment_value_names_the_variable(project):
    with pytest.raises(ConfigError) as exc:
        load(project, {"REEL__WORKERS__COUNT": "many"})
    assert "workers.count" in str(exc.value)
    assert "REEL__WORKERS__COUNT" in str(exc.value)


def test_unknown_environment_setting_is_rejected(project):
    with pytest.raises(ConfigError, match="REEL__WORKERS__COLOR"):
        load(project, {"REEL__WORKERS__COLOR": "red"})


def test_dotenv_is_read_and_real_environment_wins(project):
    (project / ".env").write_text(
        "SLACK_BOT_TOKEN=from-dotenv\nCORE_ENGINE_API_TOKEN=from-dotenv\nREEL__WORKERS__COUNT=3\n",
        encoding="utf-8",
    )
    cfg = load(project, {"CORE_ENGINE_API_TOKEN": "from-environment"})
    assert cfg.secrets.get("SLACK_BOT_TOKEN") == "from-dotenv"
    assert cfg.secrets.get("CORE_ENGINE_API_TOKEN") == "from-environment"
    assert cfg.settings.workers.count == 3


def test_secret_values_never_appear_in_repr(project):
    (project / ".env").write_text("SLACK_BOT_TOKEN=xoxb-very-secret\n", encoding="utf-8")
    cfg = load(project)
    assert "xoxb-very-secret" not in repr(cfg)
    assert "xoxb-very-secret" not in str(cfg.secrets)
    assert "SLACK_BOT_TOKEN" in repr(cfg.secrets)


def test_config_outside_a_config_folder_uses_its_own_folder(tmp_path):
    (tmp_path / "groups.toml").write_text('[groups."A"]\n', encoding="utf-8")
    (tmp_path / "reel.toml").write_text('[paths]\ngroups_file = "groups.toml"\n', encoding="utf-8")
    cfg = load_config(tmp_path / "reel.toml", environ={})
    assert cfg.paths.base_dir == tmp_path.resolve()


# --- reloading -----------------------------------------------------------------------------


def test_provider_returns_the_same_snapshot_until_a_file_changes(project, write_config):
    provider = ConfigProvider(project / "config" / "reel.toml", environ={})
    first = provider.get()
    assert provider.get() is first
    write_config("[workers]\ncount = 5\n")
    assert provider.get().settings.workers.count == 5


def test_provider_keeps_the_last_good_config_after_a_bad_edit(project, write_config):
    provider = ConfigProvider(project / "config" / "reel.toml", environ={})
    first = provider.get()
    write_config("[workers]\ncount = 0\n")
    assert provider.get() is first
    assert "workers.count" in provider.last_error
    write_config("[workers]\ncount = 3\n")
    assert provider.get().settings.workers.count == 3
    assert provider.last_error is None


def test_provider_rereads_the_groups_file(project, write_config):
    provider = ConfigProvider(project / "config" / "reel.toml", environ={})
    assert provider.get().groups.find("Java 2") is None
    write_config('[groups."Java 2"]\n', name="groups.toml")
    assert provider.get().groups.find("java-2")[0] == "Java 2"


def test_provider_raises_when_the_first_load_fails(tmp_path):
    with pytest.raises(ConfigError):
        ConfigProvider(tmp_path / "missing.toml", environ={}).get()
