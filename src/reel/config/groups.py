"""Group mapping file ``config/groups.toml`` (FR-3, FR-34, D-018)."""

import re
import unicodedata
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

_SEPARATORS = re.compile(r"[\s_\-]+")


def normalize_group_key(name: str) -> str:
    """Key used to match folder names to groups.

    Letter case is ignored (also for Armenian), and spaces, ``_`` and ``-`` count as the
    same separator, so "Python 12", "python_12" and "PYTHON-12" all match.
    """
    return _SEPARATORS.sub(" ", unicodedata.normalize("NFC", name)).strip().casefold()


class GroupConfig(BaseModel):
    """Where one group's videos go. IDs may stay empty while an integration is in dry-run."""

    model_config = ConfigDict(extra="forbid", frozen=True, coerce_numbers_to_str=True)

    aliases: tuple[str, ...] = ()
    youtube_playlist_id: str = ""
    slack_channel: str = ""
    classroom_course_id: str = ""
    core_engine_group_id: str = ""  # empty = use the group name
    vocabulary: str = ""  # words that help speech recognition (code terms, names)


class GroupsFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    groups: dict[str, GroupConfig] = {}

    @model_validator(mode="after")
    def _names_are_unique(self) -> Self:
        owner: dict[str, str] = {}
        for name, group in self.groups.items():
            for label in (name, *group.aliases):
                key = normalize_group_key(label)
                if not key:
                    raise ValueError(f"group {name!r}: names and aliases must not be empty")
                if owner.get(key, name) != name:
                    raise ValueError(f"{label!r} matches both group {owner[key]!r} and {name!r}")
                owner[key] = name
        return self

    def find(self, folder_name: str) -> tuple[str, GroupConfig] | None:
        """Return ``(group name, settings)`` for a folder name, or None if no group matches."""
        key = normalize_group_key(folder_name)
        for name, group in self.groups.items():
            if key in {normalize_group_key(label) for label in (name, *group.aliases)}:
                return name, group
        return None
