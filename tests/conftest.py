"""Shared test fixtures."""

import os
import shutil
from pathlib import Path

import pytest

from reel.logging_setup import reset_logging

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "reel.example.toml"
EXAMPLE_GROUPS = PROJECT_ROOT / "config" / "groups.example.toml"


@pytest.fixture(autouse=True)
def _reset_reel_logging():
    """Every test starts without Reel's log handlers or registered secrets."""
    yield
    reset_logging(forget_secrets=True)


def bump_mtime(path: Path) -> None:
    """Move the modification time forward so change detection sees every edit."""
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A temporary project folder with the example configuration copied in."""
    (tmp_path / "config" / "prompts").mkdir(parents=True)
    for name in ("inbox", "data", "logs", "models"):
        (tmp_path / name).mkdir()
    shutil.copyfile(EXAMPLE_CONFIG, tmp_path / "config" / "reel.toml")
    shutil.copyfile(EXAMPLE_GROUPS, tmp_path / "config" / "groups.toml")
    return tmp_path


@pytest.fixture
def db(tmp_path: Path):
    """An initialised Reel database in a temporary folder."""
    from reel.db import init_database, open_database

    path = tmp_path / "db" / "reel.db"
    init_database(path)
    database = open_database(path)
    yield database
    database.dispose()


@pytest.fixture
def write_config(project: Path):
    """Replace the project's config/reel.toml (or another file under config/) with text."""

    def write(text: str, name: str = "reel.toml") -> Path:
        path = project / "config" / name
        path.write_text(text, encoding="utf-8")
        bump_mtime(path)
        return path

    return write
