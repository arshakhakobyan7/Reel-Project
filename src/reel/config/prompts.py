"""LLM prompt templates stored as text files in ``config/prompts`` (FR-34, NFR-9)."""

import re
import string
from pathlib import Path

_NAME = re.compile(r"^[a-z0-9_]+$")


class PromptError(Exception):
    """A prompt file is missing or cannot be filled in."""


class PromptLibrary:
    """Prompt files ``<name>.txt`` with ``$placeholders``.

    Files are read on every use, so an edited prompt applies to the next run without a
    restart. ``string.Template`` syntax keeps JSON examples with ``{ }`` usable as-is.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def path(self, name: str) -> Path:
        if not _NAME.match(name):
            raise PromptError(f"invalid prompt name {name!r}: use lowercase letters, digits and _")
        return self.directory / f"{name}.txt"

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def render(self, name: str, **values: object) -> str:
        path = self.path(name)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise PromptError(f"prompt {name!r} not found: {path}") from None
        try:
            return string.Template(text).substitute({key: str(v) for key, v in values.items()})
        except KeyError as exc:
            raise PromptError(
                f"prompt {name!r} uses ${exc.args[0]} but no value was given"
            ) from None
        except ValueError as exc:
            raise PromptError(f"prompt {name!r} has an invalid placeholder: {exc}") from None
