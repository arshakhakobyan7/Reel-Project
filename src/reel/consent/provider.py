"""Consent port and common validation (T-601, D-008, D-019).

A nonempty roster with all students granted passes. Denied, missing, null or
unrecognised student status means opted_out. Unknown groups/empty rosters are unknown;
transport, authentication and malformed-data failures are unreachable. Nothing else passes.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn

from reel.db.enums import ConsentResult

if TYPE_CHECKING:
    from reel.config.loader import LoadedConfig

MAX_JSON_BYTES = 1_048_576


class StudentStatus(StrEnum):
    """Minimal consent evidence; no personal profile fields are retained."""

    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StudentConsent:
    """One opaque student identifier and its normalised status."""

    id: str
    status: StudentStatus


@dataclass(frozen=True)
class ConsentDecision:
    """Consent evidence ready for T-602 to persist in a ConsentCheck."""

    result: ConsentResult
    group_ref: str | None
    students: tuple[StudentConsent, ...]
    message: str

    def students_json(self) -> list[dict[str, str]]:
        """Return only id/status for ConsentCheck.students."""
        return [{"id": student.id, "status": student.status.value} for student in self.students]


class ConsentProvider(ABC):
    """Read consent for a group and optional lesson date; never infer approval."""

    name: ClassVar[str]

    @abstractmethod
    def check(self, group: str, lesson_date: date | None) -> ConsentDecision:
        """Return passed, opted_out, unknown or unreachable using the module's rules."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _bad_constant(value: str) -> NoReturn:
    raise ValueError("nonfinite JSON number")


def _decode_json(raw: bytes) -> Any:
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("consent JSON is too large")
    return json.loads(
        raw.decode("utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=_bad_constant
    )


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 200
        and value == value.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _parse_record(
    value: Any, *, default_ref: str | None = None
) -> tuple[str, tuple[StudentConsent, ...]]:
    if not isinstance(value, dict) or not {"students"} <= value.keys() <= {"students", "group_ref"}:
        raise ValueError("invalid consent record")
    group_ref = value.get("group_ref", default_ref)
    if not _identifier(group_ref) or not isinstance(value["students"], list):
        raise ValueError("invalid consent record")
    students: list[StudentConsent] = []
    seen: set[str] = set()
    for item in value["students"]:
        if not isinstance(item, dict) or not {"id"} <= item.keys() <= {"id", "status"}:
            raise ValueError("invalid student entry")
        student_id = item["id"]
        if not _identifier(student_id) or student_id in seen:
            raise ValueError("invalid or duplicate student id")
        seen.add(student_id)
        raw_status = item.get("status")
        if raw_status is not None and not isinstance(raw_status, str):
            raise ValueError("invalid student status type")
        status = (
            StudentStatus(raw_status)
            if raw_status in {"granted", "denied", "unknown"}
            else StudentStatus.UNKNOWN
        )
        students.append(StudentConsent(student_id, status))
    return group_ref, tuple(students)


def _decision(group_ref: str, students: tuple[StudentConsent, ...]) -> ConsentDecision:
    if not students:
        return ConsentDecision(
            ConsentResult.UNKNOWN,
            group_ref,
            (),
            "The group has no student evidence; consent is unverified.",
        )
    if any(student.status is not StudentStatus.GRANTED for student in students):
        return ConsentDecision(
            ConsentResult.OPTED_OUT,
            group_ref,
            students,
            "At least one student denied consent or has an unknown status (D-019).",
        )
    return ConsentDecision(
        ConsentResult.PASSED,
        group_ref,
        students,
        f"All {len(students)} student(s) granted consent.",
    )


def make_consent_provider(cfg: LoadedConfig, *, dry_run: bool = False) -> ConsentProvider:
    """Select the configured provider; dry_run suppresses remote HTTP without approving."""
    from reel.consent.file_provider import FileConsentProvider
    from reel.consent.http_provider import HttpConsentProvider

    settings = cfg.settings.consent
    if settings.provider == "file":
        return FileConsentProvider(cfg.paths.consent_file)
    return HttpConsentProvider(
        settings.http_base_url,
        token=cfg.secrets.get("CORE_ENGINE_API_TOKEN"),
        timeout_seconds=settings.timeout_seconds,
        dry_run=dry_run,
    )
