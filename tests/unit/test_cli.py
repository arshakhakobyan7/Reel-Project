"""Smoke tests for the package and the ``reel`` command line (T-001)."""

import subprocess
import sys
from importlib.metadata import version

import pytest

import reel
from reel.cli import main


def test_runtime_is_python_314_or_newer():
    assert sys.version_info >= (3, 14)


def test_installed_distribution_version_matches_package():
    assert version("reel") == reel.__version__


def test_version_flag_prints_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"reel {reel.__version__}"


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: reel" in capsys.readouterr().out


def test_module_entry_point_runs():
    result = subprocess.run(
        [sys.executable, "-m", "reel", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"reel {reel.__version__}"


def test_without_a_console_the_output_goes_nowhere_instead_of_failing(monkeypatch, capsys):
    """pythonw.exe (Task Scheduler, T-903) has no stdout or stderr at all."""
    import sys

    from reel.cli import use_utf8_output

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    use_utf8_output()
    assert sys.stdout is not None and sys.stderr is not None
    print("Հայերեն տեքստ")  # does not raise
    sys.stdout.close()
    sys.stderr.close()
