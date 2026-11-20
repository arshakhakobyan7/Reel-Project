"""Group mapping file ``config/groups.toml`` (FR-3, FR-34, D-018); groups typed in the
dashboard are added to it (D-061)."""

import json
import os
import re
import threading
import tomllib
import unicodedata
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

_SEPARATORS = re.compile(r"[\s_\-]+")
_WRITING = threading.Lock()
NEW_GROUP_FIELDS = (
    'youtube_playlist_id = ""           # YouTube playlist ID (starts with PL...)\n'
    'slack_channel = ""                 # Slack channel ID (for example C0123456789)\n'
    'classroom_course_id = ""           # Google Classroom course ID\n'
)


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


def add_group(path: Path, name: str, *, note: str = "") -> tuple[str, bool]:
    """Append an empty table for a new group to groups.toml (D-061).

    Staff type a group's name in the dashboard; its IDs stay empty until someone fills them
    in (they may stay empty while the integrations run in dry-run). The file's text is kept:
    comments and order do not change. Returns the group's name as the file has it, and
    whether it was added (False when the name, or an alias, already matches a group).
    Raises ValueError when the file, or the new table, is not valid.
    """
    with _WRITING:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        found = GroupsFile.model_validate(tomllib.loads(text)).find(name)
        if found is not None:
            return found[0], False
        comment = " ".join(note.split())
        header = f"[groups.{json.dumps(name, ensure_ascii=False)}]"
        block = header + (f"  # {comment}" if comment else "") + "\n" + NEW_GROUP_FIELDS
        joined = text if not text or text.endswith("\n") else text + "\n"
        updated = joined + ("\n" if joined else "") + block
        if name not in GroupsFile.model_validate(tomllib.loads(updated)).groups:
            raise ValueError(f"the group name {name!r} cannot be written to {path.name}")
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.replace(temporary, path)
        return name, True


def missing_group_message(name: str | None, lesson_missing: bool = False) -> str:
    """The hold message when a recording has no group, or one groups.toml does not have."""
    if not name:
        what = "the group and lesson number" if lesson_missing else "the group"
        return f"Set {what} of this recording, then release the job."
    lesson = ", and set the lesson number" if lesson_missing else ""
    return (
        f'The group "{name}" is not in config/groups.toml: add it there or set another '
        f"group{lesson}, then release the job."
    )
