"""Secrets come only from environment variables or the ``.env`` file, never from TOML (NFR-5).

T-701 adds the operating-system secret store (Windows Credential Manager) for OAuth tokens.
"""

from collections.abc import Mapping

from reel.config.schema import Settings

SECRET_NAMES: dict[str, str] = {
    "REEL_WEB_SESSION_SECRET": "signs staff dashboard sessions",
    "REEL_API_TOKENS": "tokens accepted by the integration API (comma-separated)",
    "SLACK_BOT_TOKEN": "Slack bot token (xoxb-...)",
    "GOOGLE_CLIENT_SECRETS_FILE": "path to the Google OAuth client JSON file",
    "CORE_ENGINE_API_TOKEN": "Core Engine API token",
    "LIBRARY_API_TOKEN": "Academy Library API token",
}


class Secrets:
    """Secret values by name. ``repr``/``str`` show which names are set, never the values."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, str]) -> None:
        unknown = sorted(set(values) - set(SECRET_NAMES))
        if unknown:
            raise KeyError(f"unknown secret name(s): {', '.join(unknown)}")
        self._values = {name: value for name, value in values.items() if value}

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Secrets:
        return cls({name: (env.get(name) or "").strip() for name in SECRET_NAMES})

    def get(self, name: str) -> str | None:
        if name not in SECRET_NAMES:
            raise KeyError(f"unknown secret name {name!r}")
        return self._values.get(name)

    def is_set(self, name: str) -> bool:
        return self.get(name) is not None

    def names_set(self) -> list[str]:
        return sorted(self._values)

    def __repr__(self) -> str:
        return f"Secrets(set={self.names_set()})"

    __str__ = __repr__


def required_secrets(settings: Settings) -> dict[str, str]:
    """Secrets needed by the enabled features, as ``{name: reason}``."""
    needed: dict[str, str] = {}
    if "live" in (settings.slack.mode, settings.notifications.mode):
        needed["SLACK_BOT_TOKEN"] = 'slack.mode or notifications.mode is "live"'
    if "live" in (settings.youtube.mode, settings.classroom.mode):
        needed["GOOGLE_CLIENT_SECRETS_FILE"] = 'youtube.mode or classroom.mode is "live"'
    if settings.consent.provider == "http":
        needed["CORE_ENGINE_API_TOKEN"] = 'consent.provider is "http"'
    if settings.library.provider == "http":
        needed["LIBRARY_API_TOKEN"] = 'library.provider is "http"'
    return needed
