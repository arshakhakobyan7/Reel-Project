"""Values that staff type into the dashboard's forms (T-807, T-804).

Both fields are plain text in the browser. A number field (``type="number"``) accepts no
character from the Armenian keyboard layout, whose number row types letters, so the field
looked broken; plain text shows what was typed and the server explains what is wrong.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from reel.config.groups import GroupsFile, add_group
from reel.config.loader import LoadedConfig
from reel.config.schema import MetadataSettings
from reel.db import normalize_username
from reel.metadata.text import Chapter, ChapterError, check_chapters, format_timestamp
from reel.web import texts
from reel.web.security import MIN_PASSWORD_LENGTH, PasswordError, check_password_rules

log = logging.getLogger("reel.web")

MAX_GROUP_LENGTH = 100
_SPACES = re.compile(r"\s+")
_NUMBER = re.compile(r"\d{1,5}")
MAX_CHAPTERS = 100
# "4:30 Ֆունկցիաներ", "1:02:30 - Օրինակներ", "(0:00) Ներածություն"
_CHAPTER = re.compile(r"^\(?(\d{1,3}(?::\d{2}){1,2})\)?\s*(?:[-–—:.|]\s*)?(.*)$")


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


def _seconds(clock: str) -> int | None:
    parts = [int(part) for part in clock.split(":")]
    if any(part >= 60 for part in parts[1:]) or (len(parts) == 3 and parts[0] > 99):
        return None
    total = 0
    for part in parts:
        total = total * 60 + part
    return total


def parse_chapters(text: str, duration: float, settings: MetadataSettings) -> list[Chapter]:
    """The chapters staff type for a video whose speech was not clear (T-1012), one per line:
    the start in the cleaned video (as in the dashboard's player), then the title. The list
    must follow YouTube's rules. Raises ValueError with an Armenian message."""
    chapters: list[Chapter] = []
    for number, line in enumerate((text or "").splitlines(), 1):
        line = _SPACES.sub(" ", unicodedata.normalize("NFC", line)).strip()
        if not line:
            continue
        found = _CHAPTER.match(line)
        start = _seconds(found.group(1)) if found else None
        title = found.group(2).strip() if found else ""
        if start is None or not title:
            raise ValueError(
                f"Տող {number}. «{line[:60]}» հասկանալի չէ։ Գրեք սկզբի ժամանակը և բաժնի "
                "անունը, օրինակ՝ «4:30 Ֆունկցիաներ»։"
            )
        chapters.append(Chapter(float(start), title))
    if len(chapters) < settings.min_chapters:
        raise ValueError(
            f"Նշեք առնվազն {settings.min_chapters} բաժին. YouTube-ը ավելի քիչ բաժին ցույց չի տալիս։"
        )
    if len(chapters) > MAX_CHAPTERS:
        raise ValueError(f"Շատ բաժիններ են (առավելագույնը {MAX_CHAPTERS})։")
    long, minimum = duration >= 3600, settings.min_chapter_seconds
    if chapters[0].start > minimum:
        raise ValueError("Առաջին բաժինը պետք է սկսվի 0:00-ից։")
    for chapter in chapters:
        if chapter.start >= duration:
            raise ValueError(
                f"«{chapter.title}» բաժինը ({format_timestamp(chapter.start, long=long)}) "
                f"տեսանյութից դուրս է. տեսանյութը {format_timestamp(duration, long=long)} է։"
            )
    for before, after in zip(chapters, chapters[1:], strict=False):
        if after.start <= before.start:
            raise ValueError(
                f"Բաժինները գրեք ժամանակի հերթականությամբ. «{after.title}»-ը "
                f"({format_timestamp(after.start, long=long)}) «{before.title}»-ից առաջ է։"
            )
    for chapter, end in zip(chapters, [c.start for c in chapters[1:]] + [duration], strict=True):
        if end - chapter.start < minimum:
            raise ValueError(f"«{chapter.title}» բաժինը պետք է տևի առնվազն {minimum:g} վայրկյան։")
    try:
        return check_chapters(chapters, duration, settings)
    except ChapterError as exc:  # a rule not explained above
        raise ValueError(str(exc)) from exc


# --- registration (T-1013) ---------------------------------------------------------------

MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 64  # the email is the account's username
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE = re.compile(r"\+?[0-9][0-9 ()-]{3,23}[0-9]")


@dataclass(frozen=True)
class Registration:
    first_name: str
    last_name: str
    email: str
    phone: str | None
    password: str

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


def _name(text: str, what: str) -> str:
    name = _SPACES.sub(" ", unicodedata.normalize("NFC", text or "")).strip()
    if not name:
        raise ValueError(f"Գրեք ձեր {what}։")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"{what.capitalize()} շատ երկար է (առավելագույնը {MAX_NAME_LENGTH} նիշ)։")
    return name


@dataclass(frozen=True)
class ProfileDetails:
    first_name: str
    last_name: str
    email: str
    phone: str | None

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


def _email(text: str, *, required: bool) -> str:
    address = unicodedata.normalize("NFC", text or "").strip().casefold()
    if not address and not required:
        return ""
    if not _EMAIL.fullmatch(address):
        raise ValueError("Email-ը սխալ է. օրինակ՝ anna@example.com։")
    if len(address) > MAX_EMAIL_LENGTH:
        raise ValueError(f"Email-ը շատ երկար է (առավելագույնը {MAX_EMAIL_LENGTH} նիշ)։")
    try:
        normalize_username(address)
    except ValueError:
        raise ValueError("Email-ում թույլատրված են տառեր, թվեր և . _ @ + - նշանները։") from None
    return address


def _phone(text: str) -> str | None:
    number = _SPACES.sub(" ", (text or "").strip())
    if number and not (_PHONE.fullmatch(number) and sum(c.isdigit() for c in number) >= 5):
        raise ValueError("Հեռախոսահամարը սխալ է. օրինակ՝ +374 91 123456։")
    return number or None


def parse_profile(
    first_name: str, last_name: str, email: str, phone: str, *, email_required: bool
) -> ProfileDetails:
    """Validate account details using registration's rules and Armenian errors."""
    first, last = _name(first_name, "անունը"), _name(last_name, "ազգանունը")
    address = _email(email, required=email_required)
    number = _phone(phone)
    return ProfileDetails(first, last, address, number)


def parse_new_password(password: str, again: str, *, current: str | None = None) -> str:
    """Validate confirmation and strength, then reject an unchanged password."""
    if password != again:
        raise ValueError(texts.PROFILE_PASSWORD_MISMATCH)
    try:
        check_password_rules(password)
    except PasswordError:
        raise ValueError(texts.PROFILE_PASSWORD_RULES.format(minimum=MIN_PASSWORD_LENGTH)) from None
    if current is not None and password == current:
        raise ValueError(texts.PROFILE_PASSWORD_UNCHANGED)
    return password


def parse_registration(
    first_name: str, last_name: str, email: str, phone: str, password: str, again: str
) -> Registration:
    """What a new user typed in the registration form. Raises ValueError (Armenian)."""
    details = parse_profile(first_name, last_name, email, phone, email_required=True)
    new = parse_new_password(password, again)
    return Registration(
        details.first_name,
        details.last_name,
        details.email,
        details.phone,
        new,
    )
