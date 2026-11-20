"""Find, read, validate and reload the configuration files (FR-34, NFR-5, NFR-9)."""

import hashlib
import logging
import os
import shutil
import threading
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import ValidationError

from reel.config.groups import GroupsFile
from reel.config.prompts import PromptLibrary
from reel.config.schema import Settings
from reel.config.secrets import Secrets

log = logging.getLogger(__name__)

CONFIG_ENV_VAR = "REEL_CONFIG"
OVERRIDE_PREFIX = "REEL__"
DEFAULT_CONFIG = Path("config") / "reel.toml"


class ConfigError(Exception):
    """The configuration cannot be found, read or validated. The message is shown to users."""


@dataclass(frozen=True)
class ResolvedPaths:
    """Absolute paths derived from the settings (relative paths use the project folder)."""

    base_dir: Path
    data_dir: Path
    database_file: Path
    logs_dir: Path
    models_dir: Path
    groups_file: Path
    prompts_dir: Path
    ffmpeg: Path
    ffprobe: Path
    ollama: Path
    watch_folders: tuple[Path, ...]
    consent_file: Path
    dotenv_file: Path


@dataclass(frozen=True)
class LoadedConfig:
    """One consistent snapshot of settings, groups, paths and secrets."""

    config_file: Path
    settings: Settings
    groups: GroupsFile
    paths: ResolvedPaths
    secrets: Secrets
    overrides: Mapping[str, str] = field(default_factory=dict)  # "section.key" -> variable

    @property
    def prompts(self) -> PromptLibrary:
        return PromptLibrary(self.paths.prompts_dir)

    def resolve(self, value: str) -> Path:
        return resolve_path(self.paths.base_dir, value)


def find_config_file(
    cli_value: str | None = None, environ: Mapping[str, str] | None = None
) -> Path:
    """Return the ``--config`` value, else ``$REEL_CONFIG``, else ``./config/reel.toml``."""
    env = os.environ if environ is None else environ
    if cli_value:
        return Path(cli_value)
    if env.get(CONFIG_ENV_VAR):
        return Path(env[CONFIG_ENV_VAR])
    return Path.cwd() / DEFAULT_CONFIG


def load_config(config_file: Path, environ: Mapping[str, str] | None = None) -> LoadedConfig:
    """Read and validate the configuration. ``environ`` defaults to ``os.environ``.

    Real environment variables take precedence over values from the ``.env`` file.
    """
    config_file = Path(config_file).resolve()
    if not config_file.is_file():
        raise ConfigError(
            f"configuration file not found: {config_file}\n"
            "Run `reel config init` in the project folder, or pass --config / set REEL_CONFIG."
        )
    real_env = dict(os.environ if environ is None else environ)
    raw = read_toml(config_file)
    configured_base = real_env.get("REEL__PATHS__BASE_DIR") or raw.get("paths", {}).get("base_dir")
    base_dir = _base_dir(config_file, configured_base if isinstance(configured_base, str) else "")
    dotenv_file = base_dir / ".env"
    env = {**read_dotenv(dotenv_file), **real_env}

    overrides = apply_overrides(raw, env)
    try:
        settings = Settings.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc, config_file, overrides)) from None

    paths = _resolve_paths(settings, base_dir, dotenv_file)
    groups = load_groups(paths.groups_file)
    return LoadedConfig(config_file, settings, groups, paths, Secrets.from_env(env), overrides)


def load_groups(path: Path) -> GroupsFile:
    if not path.is_file():
        raise ConfigError(
            f"groups file not found: {path}\n"
            "Create it from config/groups.example.toml (`reel config init`)."
        )
    raw = read_toml(path)
    try:
        return GroupsFile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc, path, {})) from None


def read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML syntax error: {exc}") from None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values = dotenv_values(path, encoding="utf-8")
    return {key: value for key, value in values.items() if value is not None}


def apply_overrides(raw: dict[str, Any], env: Mapping[str, str]) -> dict[str, str]:
    """Apply ``REEL__SECTION__KEY=value`` variables to the raw settings.

    Returns ``{"section.key": variable name}`` so errors can name the variable.
    """
    applied: dict[str, str] = {}
    for name in sorted(env):
        if not name.upper().startswith(OVERRIDE_PREFIX):
            continue
        keys = [part.lower() for part in name[len(OVERRIDE_PREFIX) :].split("__")]
        if not all(keys):
            raise ConfigError(f"environment variable {name}: use the form REEL__SECTION__KEY")
        node = raw
        for key in keys[:-1]:
            child = node.setdefault(key, {})
            if not isinstance(child, dict):
                raise ConfigError(
                    f"environment variable {name}: {key!r} is a setting, not a section"
                )
            node = child
        node[keys[-1]] = parse_env_value(env[name])
        applied[".".join(keys)] = name
    return applied


def parse_env_value(text: str) -> Any:
    """Read an environment value as a TOML value (number, boolean, list, ...), else as text."""
    if "\n" in text or "\r" in text:
        return text
    try:
        return tomllib.loads(f"value = {text}")["value"]
    except tomllib.TOMLDecodeError:
        return text


def format_validation_error(
    exc: ValidationError, source: Path, overrides: Mapping[str, str]
) -> str:
    """One line per problem, naming the setting; values of unknown keys are not echoed."""
    lines = [f"{source}: invalid configuration"]
    for error in exc.errors(include_url=False):
        loc = [str(part) for part in error["loc"]]
        key = ".".join(loc) or "(top level)"
        message = error["msg"].removeprefix("Value error, ")
        if error["type"] == "extra_forbidden":
            message = "unknown setting - check the spelling (secrets belong in .env, not here)"
        detail = ""
        value = error.get("input")
        if error["type"] not in {"missing", "extra_forbidden"} and not isinstance(
            value, dict | list | tuple
        ):
            detail = f" (got {value!r})"
        origin = ""
        for size in range(len(loc), 0, -1):
            variable = overrides.get(".".join(loc[:size]))
            if variable:
                origin = f" [set by environment variable {variable}]"
                break
        lines.append(f"  - {key}: {message}{detail}{origin}")
    return "\n".join(lines)


def resolve_path(base: Path, value: str) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def resolve_executable(base: Path, value: str) -> Path:
    """A value with a folder part is a path; a bare name (``ffmpeg``) is looked up on PATH."""
    if "/" in value or "\\" in value:
        return resolve_path(base, value)
    found = shutil.which(value)
    return Path(found) if found else Path(value)


def _base_dir(config_file: Path, configured: str) -> Path:
    if configured:
        return resolve_path(config_file.parent, configured)
    folder = config_file.parent
    return folder.parent if folder.name.lower() == "config" else folder


def _resolve_paths(settings: Settings, base: Path, dotenv_file: Path) -> ResolvedPaths:
    p = settings.paths
    data_dir = resolve_path(base, p.data_dir)
    return ResolvedPaths(
        base_dir=base,
        data_dir=data_dir,
        database_file=resolve_path(base, p.database_file)
        if p.database_file
        else data_dir / "reel.db",
        logs_dir=resolve_path(base, p.logs_dir),
        models_dir=resolve_path(base, p.models_dir),
        groups_file=resolve_path(base, p.groups_file),
        prompts_dir=resolve_path(base, p.prompts_dir),
        ffmpeg=resolve_executable(base, p.ffmpeg),
        ffprobe=resolve_executable(base, p.ffprobe),
        ollama=resolve_executable(base, p.ollama),
        watch_folders=tuple(resolve_path(base, f) for f in settings.ingest.watch_folders),
        consent_file=resolve_path(base, settings.consent.file_path),
        dotenv_file=dotenv_file,
    )


class ConfigProvider:
    """Hands out the current configuration and re-reads it when a file changes (NFR-9).

    Stages call ``get()`` when they start. If an edited file is invalid, the last valid
    configuration stays in use and the problem is kept in ``last_error`` (and logged),
    so a typo does not break running jobs.
    """

    def __init__(self, config_file: Path, environ: Mapping[str, str] | None = None) -> None:
        self.config_file = Path(config_file).resolve()
        self._environ = environ
        self._lock = threading.Lock()
        self._current: LoadedConfig | None = None
        self._watched: tuple[Path, ...] = (self.config_file,)
        self._stamp: tuple[tuple[str, str | None], ...] | None = None
        self.last_error: str | None = None

    def get(self) -> LoadedConfig:
        with self._lock:
            stamp = _stamp(self._watched)
            if self._current is not None and stamp == self._stamp:
                return self._current
            try:
                loaded = load_config(self.config_file, self._environ)
            except ConfigError as exc:
                if self._current is None:
                    raise
                if str(exc) != self.last_error:
                    log.error("Configuration change rejected; keeping the previous one: %s", exc)
                self.last_error = str(exc)
                self._stamp = stamp
                return self._current
            self._current = loaded
            self.last_error = None
            self._watched = (self.config_file, loaded.paths.groups_file, loaded.paths.dotenv_file)
            self._stamp = _stamp(self._watched)
            return loaded


def _stamp(files: Iterable[Path]) -> tuple[tuple[str, str | None], ...]:
    """Content fingerprint of the watched files.

    Content (not modification time) is compared: Windows file times advance in ~15 ms
    steps, so two quick edits of equal size could otherwise look unchanged. The files are
    a few kilobytes, so reading them at the start of each stage costs nothing noticeable.
    """
    result = []
    for path in files:
        try:
            digest = hashlib.blake2b(path.read_bytes(), digest_size=16).hexdigest()
        except FileNotFoundError:
            digest = None
        result.append((str(path), digest))
    return tuple(result)
