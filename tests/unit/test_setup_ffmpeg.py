"""Tests for scripts/setup_ffmpeg.py with a fake build archive (T-002). No network access."""

import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_ffmpeg.py"
TOP = "ffmpeg-1.0-essentials_build"


@pytest.fixture(scope="module")
def setup_ffmpeg():
    spec = importlib.util.spec_from_file_location("setup_ffmpeg", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_archive(path: Path, members: tuple[str, ...]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for member in members:
            zf.writestr(f"{TOP}/{member}", f"content of {member}")
    return path


@pytest.fixture
def env(tmp_path, setup_ffmpeg):
    """A fake build archive, its spec, and a downloader that counts calls."""
    archive = make_archive(
        tmp_path / "source.zip",
        ("bin/ffmpeg.exe", "bin/ffprobe.exe", "bin/ffplay.exe", "LICENSE", "README.txt", "doc/x"),
    )
    build = setup_ffmpeg.BuildSpec(
        version="1.0",
        url=f"https://example.invalid/{TOP}.zip",
        sha256=setup_ffmpeg.sha256_of(archive),
        size=archive.stat().st_size,
    )
    calls = []

    def downloader(url, target, size):
        calls.append(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, target)

    return {
        "build": build,
        "dest": tmp_path / "tools" / "ffmpeg",
        "downloads": tmp_path / "tools" / ".downloads",
        "downloader": downloader,
        "calls": calls,
        "archive": archive,
    }


def run_install(setup_ffmpeg, env, **kwargs):
    return setup_ffmpeg.install(
        kwargs.pop("build", env["build"]),
        env["dest"],
        env["downloads"],
        downloader=kwargs.pop("downloader", env["downloader"]),
        **kwargs,
    )


def test_install_extracts_only_needed_files(setup_ffmpeg, env):
    assert run_install(setup_ffmpeg, env) == "installed"
    dest = env["dest"]
    installed = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    assert installed == [
        "INSTALL.json",
        "LICENSE",
        "README.txt",
        "bin/ffmpeg.exe",
        "bin/ffprobe.exe",
    ]
    manifest = json.loads((dest / "INSTALL.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.0"
    assert manifest["sha256"] == env["build"].sha256
    assert set(manifest["files"]) == {"bin/ffmpeg.exe", "bin/ffprobe.exe", "LICENSE", "README.txt"}
    assert not env["downloads"].exists(), "archive and empty download folder are removed"


def test_second_run_does_nothing(setup_ffmpeg, env):
    run_install(setup_ffmpeg, env)

    def must_not_download(*args):
        raise AssertionError("download must not happen when already installed")

    assert run_install(setup_ffmpeg, env, downloader=must_not_download) == "already-installed"
    assert len(env["calls"]) == 1


def test_checksum_mismatch_fails_and_installs_nothing(setup_ffmpeg, env):
    bad = setup_ffmpeg.BuildSpec("1.0", env["build"].url, "0" * 64, env["build"].size)
    with pytest.raises(setup_ffmpeg.ChecksumError, match="SHA-256 mismatch"):
        run_install(setup_ffmpeg, env, build=bad)
    assert not env["dest"].exists()
    assert not (env["downloads"] / bad.archive_name).exists(), "bad download is deleted"


def test_changed_binary_is_detected_and_reinstalled(setup_ffmpeg, env):
    run_install(setup_ffmpeg, env)
    (env["dest"] / "bin" / "ffmpeg.exe").write_text("tampered")
    assert not setup_ffmpeg.is_installed(env["dest"], env["build"])
    assert run_install(setup_ffmpeg, env) == "installed"
    assert len(env["calls"]) == 2
    assert setup_ffmpeg.is_installed(env["dest"], env["build"])


def test_force_reinstalls(setup_ffmpeg, env):
    run_install(setup_ffmpeg, env)
    assert run_install(setup_ffmpeg, env, force=True) == "installed"
    assert len(env["calls"]) == 2


def test_archive_without_ffprobe_is_rejected(setup_ffmpeg, env, tmp_path):
    archive = make_archive(tmp_path / "incomplete.zip", ("bin/ffmpeg.exe", "LICENSE"))
    build = setup_ffmpeg.BuildSpec(
        "1.0", env["build"].url, setup_ffmpeg.sha256_of(archive), archive.stat().st_size
    )

    def downloader(url, target, size):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, target)

    with pytest.raises(setup_ffmpeg.SetupError, match=r"bin/ffprobe\.exe"):
        run_install(setup_ffmpeg, env, build=build, downloader=downloader)
    assert not env["dest"].exists()
    assert not env["dest"].with_name("ffmpeg.staging").exists()


def test_failed_verification_keeps_previous_install(setup_ffmpeg, env):
    run_install(setup_ffmpeg, env)

    def failing_verifier(ffmpeg, ffprobe):
        raise setup_ffmpeg.SetupError("missing filters")

    with pytest.raises(setup_ffmpeg.SetupError, match="missing filters"):
        run_install(setup_ffmpeg, env, force=True, verifier=failing_verifier)
    assert setup_ffmpeg.is_installed(env["dest"], env["build"])
    assert not env["dest"].with_name("ffmpeg.staging").exists()


def test_pinned_build_is_consistent(setup_ffmpeg):
    pinned = setup_ffmpeg.PINNED_BUILD
    assert pinned.version in pinned.archive_name
    assert pinned.url.startswith("https://github.com/GyanD/codexffmpeg/releases/download/")
    assert len(pinned.sha256) == 64


def busy_staging_rename(monkeypatch, failures: int | None):
    """Make renaming the staging folder fail like a file locked by antivirus.

    ``failures=None`` means it never succeeds. Returns a dict with the failures left.
    """
    real_rename = Path.rename
    state = {"left": failures}

    def rename(self, target):
        if self.name.endswith(".staging") and (state["left"] is None or state["left"] > 0):
            if state["left"] is not None:
                state["left"] -= 1
            raise PermissionError(5, "Access is denied")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    return state


def test_busy_folder_move_is_retried(setup_ffmpeg, env, monkeypatch):
    monkeypatch.setattr(setup_ffmpeg, "RETRY_DELAY", 0)
    state = busy_staging_rename(monkeypatch, failures=3)
    assert run_install(setup_ffmpeg, env) == "installed"
    assert state["left"] == 0
    assert setup_ffmpeg.is_installed(env["dest"], env["build"])


def test_busy_folder_gives_clear_error(setup_ffmpeg, env, monkeypatch):
    monkeypatch.setattr(setup_ffmpeg, "RETRY_DELAY", 0)
    monkeypatch.setattr(setup_ffmpeg, "RETRY_ATTEMPTS", 3)
    busy_staging_rename(monkeypatch, failures=None)
    with pytest.raises(setup_ffmpeg.SetupError, match="another program"):
        run_install(setup_ffmpeg, env)
    assert not env["dest"].exists()


def test_failed_swap_restores_previous_install(setup_ffmpeg, env, monkeypatch):
    run_install(setup_ffmpeg, env)
    monkeypatch.setattr(setup_ffmpeg, "RETRY_DELAY", 0)
    monkeypatch.setattr(setup_ffmpeg, "RETRY_ATTEMPTS", 3)
    busy_staging_rename(monkeypatch, failures=None)
    with pytest.raises(setup_ffmpeg.SetupError, match="another program"):
        run_install(setup_ffmpeg, env, force=True)
    assert setup_ffmpeg.is_installed(env["dest"], env["build"])
    assert not env["dest"].with_name("ffmpeg.old").exists()
