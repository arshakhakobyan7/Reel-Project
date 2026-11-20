"""Values that staff type into the dashboard's forms (T-807, T-804).

Both fields are plain text in the browser. A number field (``type="number"``) accepts no
character from the Armenian keyboard layout, whose number row types letters, so the field
looked broken; plain text shows what was typed and the server explains what is wrong.
"""

import logging
import re
import unicodedata
from datetime import date

from reel.config.groups import GroupsFile, add_group
from reel.config.loader import LoadedConfig

log = logging.getLogger("reel.web")

MAX_GROUP_LENGTH = 100
_SPACES = re.compile(r"\s+")
_NUMBER = re.compile(r"\d{1,5}")


def parse_group(text: str, groups: GroupsFile) -> str:
    """The group's name as configured in groups.toml when the text matches a name or an
    alias, else the typed name itself. Raises ValueError with an Armenian message."""
    name = _SPACES.sub(" ", unicodedata.normalize("NFC", text or "")).strip()
    if not name:
        raise ValueError("Գրեք խմբի անունը։")
    if len(name) > MAX_GROUP_LENGTH:
        raise ValueError(f"Խմբի անունը շատ երկար է (առավելագույնը {MAX_GROUP_LENGTH} նիշ)։")
    found = groups.find(name)
    return found[0] if found else name


def ensure_group(text: str, cfg: LoadedConfig, by: str) -> str:
    """``parse_group``; a typed group that groups.toml does not have yet is added to it at
    once, with empty IDs (D-061), so the recording does not stop for lack of a group.

    If the file cannot be changed, the typed name is still used: the job then waits at
    Chapters with a message that names the group (``missing_group_message``)."""
    name = parse_group(text, cfg.groups)
    if cfg.groups.find(name) is None:
        note = f"added in the dashboard by {by} on {date.today().isoformat()}"
        try:
            name, added = add_group(cfg.paths.groups_file, name, note=note)
            if added:
                log.info("group %r added to %s by %s", name, cfg.paths.groups_file, by)
        except OSError, ValueError:
            log.exception("group %r could not be added to %s", name, cfg.paths.groups_file)
    return name


def parse_lesson(text: str | int | None) -> int | None:
    """A lesson number of 0 or more, or None when the field is empty."""
    if text is None or isinstance(text, bool):
        return None if text is None else _not_a_number()
    value = str(text).strip()
    if not value:
        return None
    if not _NUMBER.fullmatch(value):
        _not_a_number()
    return int(value)


def _not_a_number() -> int:
    raise ValueError("Դասի համարը պետք է լինի թիվ (0, 1, 2 …)։ Թվերը գրեք լատինական ստեղնաշարով։")
