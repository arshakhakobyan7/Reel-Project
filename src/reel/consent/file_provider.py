"""Reload a minimal, case-insensitive consent registry on every check (T-601)."""

from datetime import date
from pathlib import Path

from reel.consent.provider import (
    MAX_JSON_BYTES,
    ConsentDecision,
    ConsentProvider,
    _decision,
    _decode_json,
    _identifier,
    _parse_record,
)
from reel.db.enums import ConsentResult


class FileConsentProvider(ConsentProvider):
    """Use the registry's current statuses; lesson_date has no historical effect."""

    name = "file"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def check(self, group: str, lesson_date: date | None) -> ConsentDecision:
        """Read/validate the whole file; missing or invalid files are unreachable."""
        try:
            with self.path.open("rb") as stream:
                data = _decode_json(stream.read(MAX_JSON_BYTES + 1))
        except FileNotFoundError:
            return ConsentDecision(
                ConsentResult.UNREACHABLE, None, (), "Consent registry file is missing."
            )
        except OSError:
            return ConsentDecision(
                ConsentResult.UNREACHABLE, None, (), "Consent registry file cannot be read."
            )
        except ValueError, RecursionError:
            return ConsentDecision(
                ConsentResult.UNREACHABLE,
                None,
                (),
                "Consent registry contains invalid JSON or exceeds 1 MiB.",
            )
        try:
            if not isinstance(data, dict):
                raise ValueError("registry must be an object")
            registry = {}
            for name, record in data.items():
                if not _identifier(name) or name.casefold() in registry:
                    raise ValueError("invalid or ambiguous group name")
                registry[name.casefold()] = _parse_record(record, default_ref=name)
        except ValueError:
            return ConsentDecision(
                ConsentResult.UNREACHABLE,
                None,
                (),
                "Consent registry has an invalid group/student structure.",
            )
        record = registry.get(group.strip().casefold())
        if record is None:
            return ConsentDecision(
                ConsentResult.UNKNOWN, None, (), "The group is not known to the consent registry."
            )
        return _decision(*record)
