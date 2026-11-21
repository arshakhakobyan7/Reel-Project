"""The Google Cloud setup script (T-1018): files Reel writes must belong to the reel user.

setup.sh runs as root. A file that a root shell creates (for example with ``>>``) is owned by
root, and Reel, which runs as ``reel``, then cannot write it. That stopped every LLM stage on the
teacher-demo server, because Reel appends to ``logs/ollama.log`` when it starts Ollama.
"""

from pathlib import Path

SETUP = Path(__file__).resolve().parents[2] / "deploy" / "gcp" / "setup.sh"
LOG = '"$APP/logs/ollama.log"'


def _first(lines: list[str], wanted: str) -> int:
    return next(number for number, line in enumerate(lines) if wanted in line)


def test_the_ollama_log_is_given_to_reel_before_root_opens_it():
    lines = SETUP.read_text(encoding="utf-8").splitlines()
    created = _first(lines, f"touch {LOG}")
    owned = _first(lines, f"chown reel:reel {LOG}")
    opened_by_root = _first(lines, f">>{LOG}")
    assert created < owned < opened_by_root


def test_the_log_is_prepared_even_when_the_model_is_already_there():
    # Outside the "pull the model" branch, so running setup.sh again repairs an old install.
    lines = SETUP.read_text(encoding="utf-8").splitlines()
    owned = _first(lines, f"chown reel:reel {LOG}")
    pull_check = _first(lines, "if ! as_reel env OLLAMA_MODELS=")
    assert owned < pull_check
    assert not lines[owned].startswith((" ", "\t"))
