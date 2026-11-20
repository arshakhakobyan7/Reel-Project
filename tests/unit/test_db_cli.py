"""``reel db`` commands and the database line of ``reel config check`` (T-101)."""

from pathlib import Path

from reel.cli import main
from reel.config import load_config
from reel.config.check import CheckItem, run_checks
from reel.db import init_database
from reel.media.ffmpeg_check import CapabilityReport


def db_args(project: Path, *action: str) -> list[str]:
    return ["--config", str(project / "config" / "reel.toml"), "db", *action]


def test_init_status_and_upgrade(project, capsys):
    path = project / "data" / "reel.db"
    assert main(db_args(project, "status")) == 1
    assert "does not exist yet: run `reel db init`" in capsys.readouterr().out
    assert not path.exists()

    assert main(db_args(project, "init")) == 0
    assert f"Created database {path} (schema version 1)." in capsys.readouterr().out
    assert path.is_file()

    assert main(db_args(project, "init")) == 0
    assert "already exists (schema version 1); nothing to do." in capsys.readouterr().out

    assert main(db_args(project, "status")) == 0
    out = capsys.readouterr().out
    assert "State         : current" in out
    assert "Journal mode  : wal" in out
    assert "Instance id   : " in out
    assert "  jobs                 0" in out

    assert main(db_args(project, "upgrade")) == 0
    assert "already at schema version 1" in capsys.readouterr().out
    assert not (project / "data" / "backups").exists()


def test_database_file_setting(project, write_config):
    write_config('[paths]\ndatabase_file = "state/main.db"\n')
    assert main(db_args(project, "init")) == 0
    assert (project / "state" / "main.db").is_file()
    assert not (project / "data" / "reel.db").exists()


def test_database_follows_the_data_folder_by_default(project, write_config):
    write_config('[paths]\ndata_dir = "var"\n')
    assert main(db_args(project, "init")) == 0
    assert (project / "var" / "reel.db").is_file()


def test_a_broken_database_file_is_reported(project, capsys):
    (project / "data" / "reel.db").write_text("not sqlite " * 100)
    assert main(db_args(project, "status")) == 1
    assert "cannot be read" in capsys.readouterr().out
    assert main(db_args(project, "init")) == 1
    assert "cannot be read" in capsys.readouterr().err


def test_a_broken_config_is_reported(project, write_config, capsys):
    write_config("[workers]\ncount = 0\n")
    assert main(db_args(project, "init")) == 1
    assert "workers.count" in capsys.readouterr().err


def test_config_check_reports_the_database(project):
    cfg = load_config(project / "config" / "reel.toml")

    def database_items() -> list[CheckItem]:
        items = run_checks(cfg, ffmpeg_checker=lambda *_: CapabilityReport("f", "p"))
        return [item for item in items if item.message.startswith("database")]

    shown = str(Path("data") / "reel.db")
    assert database_items() == [
        CheckItem("warn", f"database {shown} does not exist yet: run `reel db init`")
    ]
    init_database(cfg.paths.database_file)
    assert database_items() == [CheckItem("ok", f"database {shown}: schema version 1")]
    cfg.paths.database_file.unlink()
    cfg.paths.database_file.write_text("garbage " * 100)
    (item,) = database_items()
    assert item.level == "error" and "cannot be read" in item.message
