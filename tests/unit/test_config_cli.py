"""``reel config check | init | show`` and the checks behind them (T-003)."""

import json
import os
import shutil
from pathlib import Path

import pytest

from reel.cli import main
from reel.config import SECRET_NAMES, load_config
from reel.config.check import run_checks
from reel.media.ffmpeg_check import CapabilityReport, FfmpegError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FFMPEG_BIN = PROJECT_ROOT / "tools" / "ffmpeg" / "bin"
needs_ffmpeg = pytest.mark.skipif(
    not (FFMPEG_BIN / "ffmpeg.exe").is_file(),
    reason="FFmpeg not installed (scripts/setup_ffmpeg.py)",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Tests must not see REEL_* variables or secrets from the developer's machine."""
    for name in list(os.environ):
        if name.upper().startswith("REEL") or name.upper() in SECRET_NAMES:
            monkeypatch.delenv(name)


def ffmpeg_ok(ffmpeg, ffprobe):
    return CapabilityReport("ffmpeg version test", "ffprobe version test")


def ffmpeg_missing(ffmpeg, ffprobe):
    raise FfmpegError(f"{ffmpeg} was not found")


def config_args(project: Path, *action: str) -> list[str]:
    return ["--config", str(project / "config" / "reel.toml"), "config", *action]


# --- reel config check -----------------------------------------------------------------------


@needs_ffmpeg
def test_check_passes_for_the_example_config(project, monkeypatch, capsys):
    monkeypatch.setenv("REEL__PATHS__FFMPEG", str(FFMPEG_BIN / "ffmpeg.exe"))
    monkeypatch.setenv("REEL__PATHS__FFPROBE", str(FFMPEG_BIN / "ffprobe.exe"))
    code = main(config_args(project, "check"))
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Result: OK - 0 error(s), 2 warning(s)" in out
    assert "[WARN] database" in out and "run `reel db init`" in out
    assert "[WARN] consent registry" in out
    assert "[ OK ] FFmpeg: ffmpeg version 9.0.1" in out


def test_check_fails_for_a_broken_config(project, write_config, capsys):
    write_config("[workers]\ncount = 0\n")
    assert main(config_args(project, "check")) == 1
    assert "workers.count" in capsys.readouterr().err


def test_check_fails_for_a_bad_environment_override(project, monkeypatch, capsys):
    monkeypatch.setenv("REEL__WORKERS__COUNT", "0")
    assert main(config_args(project, "check")) == 1
    assert "REEL__WORKERS__COUNT" in capsys.readouterr().err


def test_checks_report_environment_problems(project, write_config):
    shutil.rmtree(project / "inbox")
    write_config('[slack]\nmode = "live"\n')
    items = run_checks(load_config(project / "config" / "reel.toml", environ={}), ffmpeg_ok)
    errors = [item.message for item in items if item.level == "error"]
    assert any("watch folder inbox does not exist" in e for e in errors)
    assert any("SLACK_BOT_TOKEN is missing" in e for e in errors)
    assert any("slack_channel is empty for group(s): Python 12" in e for e in errors)


def test_a_live_youtube_group_without_a_playlist_is_a_warning(project, write_config):
    write_config('[youtube]\nmode = "live"\n')
    items = run_checks(load_config(project / "config" / "reel.toml", environ={}), ffmpeg_ok)
    playlist = [item for item in items if "youtube_playlist_id" in item.message]
    assert [(item.level, item.message) for item in playlist] == [(
        "warn",
        "youtube_playlist_id is empty for group(s): Python 12: "
        "their videos are uploaded without a playlist",
    )]  # fmt: skip


def test_checks_report_missing_ffmpeg(project):
    items = run_checks(load_config(project / "config" / "reel.toml", environ={}), ffmpeg_missing)
    assert any(i.level == "error" and "FFmpeg" in i.message for i in items)


def test_checks_accept_a_live_setup_with_its_secrets(project, write_config):
    write_config('[slack]\nmode = "live"\n')
    write_config('[groups."Python 12"]\nslack_channel = "C123"\n', name="groups.toml")
    cfg = load_config(project / "config" / "reel.toml", environ={"SLACK_BOT_TOKEN": "xoxb-1"})
    items = run_checks(cfg, ffmpeg_ok)
    assert [i.message for i in items if i.level == "error"] == []
    assert any(i.message == "secret SLACK_BOT_TOKEN is set" for i in items)


# --- reel config init / show -----------------------------------------------------------------


def test_init_creates_the_files_once_and_never_overwrites(tmp_path, capsys):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copyfile(PROJECT_ROOT / "config" / "reel.example.toml", config_dir / "reel.example.toml")
    shutil.copyfile(
        PROJECT_ROOT / "config" / "groups.example.toml", config_dir / "groups.example.toml"
    )
    shutil.copyfile(PROJECT_ROOT / ".env.example", tmp_path / ".env.example")
    target = config_dir / "reel.toml"

    assert main(["--config", str(target), "config", "init"]) == 0
    assert target.read_bytes() == (config_dir / "reel.example.toml").read_bytes()
    assert (config_dir / "groups.toml").is_file()
    assert (tmp_path / ".env").is_file()

    target.write_text("# edited by staff\n", encoding="utf-8")
    assert main(["--config", str(target), "config", "init"]) == 0
    assert target.read_text(encoding="utf-8") == "# edited by staff\n"
    assert "kept existing" in capsys.readouterr().out


def test_show_prints_settings_but_no_secret_values(project, monkeypatch, capsys):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-hidden-value")
    monkeypatch.setenv("REEL__WORKERS__COUNT", "6")
    assert main(config_args(project, "show")) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["settings"]["workers"]["count"] == 6
    assert data["secrets_set"] == ["SLACK_BOT_TOKEN"]
    assert data["environment_overrides"] == {"workers.count": "REEL__WORKERS__COUNT"}
    assert "xoxb-hidden-value" not in out
    assert "բարև ձեզ" in out


def test_config_without_action_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["config"])
    assert exc.value.code == 2
