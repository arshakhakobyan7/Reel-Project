"""Structured logging for Reel (NFR-7).

All outputs are fed by the standard ``logging`` module:

- ``logs/reel-<role>.jsonl``: one JSON object per line (UTC time, level, component, job
  context, message, extra fields, exception), rotated by size. Every process uses its own
  role (``serve``, ``worker``, ...) so two processes never rotate the same file (Windows).
- the console: short readable lines;
- ``data/jobs/<job id>/job.log``: the readable history of one recording.

``log_context(job_id=..., stage=...)`` attaches job information to every record logged
inside the block (in the same thread). Secret values and well-known token formats are
masked in every output (NFR-5).

Typical use::

    configure_from_config(cfg, role="serve")
    log = logging.getLogger(__name__)
    with log_context(job_id=job.id, stage="transcribe"):
        log.info("Transcription finished", extra={"audio_seconds": 6600.0})
"""

import json
import logging
import logging.handlers
import re
import sys
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, TextIO

from reel.config.loader import LoadedConfig
from reel.config.schema import LoggingSettings

MASK = "[REDACTED]"
# Context fields shown first in JSON, in this order.
CONTEXT_KEYS = ("job_id", "recording_id", "stage", "attempt", "worker")
_PREFIX_KEYS = frozenset({"job_id", "stage"})  # shown as "[job 12 | transcribe]" in text
_RESERVED_KEYS = frozenset({"time", "level", "component", "message", "exception", "stack"})
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None))) | {
    "message",
    "asctime",
}
_MARK = "_reel_handler"

_context: ContextVar[Mapping[str, Any]] = ContextVar(
    "reel_log_context", default=MappingProxyType({})
)


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Attach fields (``job_id``, ``stage``, ...) to every record logged inside the block."""
    merged = {
        **_context.get(),
        **{key: value for key, value in fields.items() if value is not None},
    }
    token = _context.set(MappingProxyType(merged))
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_context.get())


class Redactor:
    """Masks secret values and well-known token formats in log output (NFR-5)."""

    MIN_LENGTH = 6  # shorter values are not registered, so ordinary words are not masked
    PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"xox[abposr]-[0-9A-Za-z-]{10,}"), MASK),  # Slack tokens
        (re.compile(r"ya29\.[0-9A-Za-z_\-]{20,}"), MASK),  # Google OAuth access tokens
        (re.compile(r"1//[0-9A-Za-z_\-]{20,}"), MASK),  # Google OAuth refresh tokens
        (re.compile(r"(?i)\b(bearer\s+)[0-9A-Za-z._~+/=\-]{8,}"), rf"\g<1>{MASK}"),
        (
            re.compile(
                r"(?i)\b((?:access_token|refresh_token|id_token|client_secret|api_?key|"
                r"password|passwd|secret|token)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;]{4,}"
            ),
            rf"\g<1>{MASK}",
        ),
    )
    SECRET_KEY = re.compile(r"(?i)(token|secret|password|passwd|authorization|api_?key|cookie)")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: set[str] = set()
        self._pattern: re.Pattern[str] | None = None

    def add(self, *values: str | None) -> None:
        with self._lock:
            for raw in values:
                value = (raw or "").strip()
                if len(value) >= self.MIN_LENGTH:
                    self._values.add(value)
            ordered = sorted(self._values, key=len, reverse=True)
            self._pattern = re.compile("|".join(map(re.escape, ordered))) if ordered else None

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._pattern = None

    def redact(self, text: str) -> str:
        pattern = self._pattern
        if pattern is not None:
            text = pattern.sub(MASK, text)
        for regex, replacement in self.PATTERNS:
            text = regex.sub(replacement, text)
        return text

    def redact_value(self, key: str | None, value: Any) -> Any:
        """Redact strings inside a JSON-like value; string values of secret-like keys are masked."""
        if isinstance(value, str):
            if key is not None and self.SECRET_KEY.search(key):
                return MASK
            return self.redact(value)
        if isinstance(value, Mapping):
            return {str(k): self.redact_value(str(k), v) for k, v in value.items()}
        if isinstance(value, list | tuple | set | frozenset):
            return [self.redact_value(None, item) for item in value]
        return value


_redactor = Redactor()


def register_secret(*values: str | None) -> None:
    """Mask these values in all log output from now on (e.g. tokens obtained at runtime)."""
    _redactor.add(*values)


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """Fields added through ``extra=`` or ``log_context`` (not standard LogRecord fields)."""
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_ATTRS and not key.startswith("_")
    }


class ContextFilter(logging.Filter):
    """Copies the current ``log_context`` fields onto each record; explicit extras win."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _context.get().items():
            if key not in record.__dict__:
                setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per record: time, level, component, context, message, extras."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        extras = _extras(record)
        created = datetime.fromtimestamp(record.created, UTC)
        data: dict[str, Any] = {
            "time": created.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "component": record.name,
        }
        for key in CONTEXT_KEYS:
            if key in extras:
                data[key] = extras.pop(key)
        data["message"] = record.getMessage()
        for key in sorted(extras):
            data[f"extra_{key}" if key in _RESERVED_KEYS else key] = extras[key]
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            data["stack"] = self.formatStack(record.stack_info)
        safe = {key: self.redactor.redact_value(key, value) for key, value in data.items()}
        return json.dumps(safe, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """``19:30:01 WARNING [job 12 | clean] reel.media: message (key=value)``."""

    def __init__(self, redactor: Redactor, *, with_date: bool = False, with_job: bool = True):
        super().__init__()
        self.redactor = redactor
        self.time_format = "%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S"
        self.with_job = with_job

    def format(self, record: logging.LogRecord) -> str:
        extras = _extras(record)
        tags = []
        if self.with_job and extras.get("job_id") is not None:
            tags.append(f"job {extras['job_id']}")
        if extras.get("stage"):
            tags.append(str(extras["stage"]))
        prefix = f"[{' | '.join(tags)}] " if tags else ""
        stamp = time.strftime(self.time_format, time.localtime(record.created))
        line = f"{stamp} {record.levelname:<7} {prefix}{record.name}: {record.getMessage()}"
        details = " ".join(
            f"{key}={value}" for key, value in sorted(extras.items()) if key not in _PREFIX_KEYS
        )
        if details:
            line += f" ({details})"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            line += "\n" + self.formatStack(record.stack_info)
        return self.redactor.redact(line)


class JobFileHandler(logging.Handler):
    """Appends records that carry a ``job_id`` to ``<jobs_dir>/<job id>/job.log``.

    The file is opened and closed for every record (job logs are small), so nothing stays
    locked and several processes can append to the same job log.
    """

    def __init__(self, jobs_dir: Path) -> None:
        super().__init__()
        self.jobs_dir = Path(jobs_dir)

    def emit(self, record: logging.LogRecord) -> None:
        job_id = getattr(record, "job_id", None)
        if job_id is None or not _SAFE_NAME.match(str(job_id)):
            return
        try:
            path = self.jobs_dir / str(job_id) / "job.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)


class _ConsoleHandler(logging.StreamHandler):
    """Writes to the current ``sys.stderr``, even if it is replaced later (e.g. by tests)."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property
    def stream(self) -> TextIO:  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, value: TextIO) -> None:
        pass


@dataclass
class LoggingSetup:
    """What ``configure_logging`` installed."""

    handlers: list[logging.Handler] = field(default_factory=list)
    json_file: Path | None = None
    json_handler: logging.handlers.RotatingFileHandler | None = None


_original_root_level: int | None = None


def configure_logging(
    settings: LoggingSettings | None = None,
    *,
    logs_dir: Path | None = None,
    jobs_dir: Path | None = None,
    role: str = "serve",
    secret_values: Iterable[str | None] = (),
    console: bool = True,
    console_level: str | None = None,
    stream: TextIO | None = None,
) -> LoggingSetup:
    """(Re)install Reel's log handlers on the root logger. Safe to call again.

    ``logs_dir`` enables ``reel-<role>.jsonl``; ``jobs_dir`` enables the per-job logs.
    """
    global _original_root_level
    if not _SAFE_NAME.match(role):
        raise ValueError(f"invalid log role {role!r}: use letters, digits, _ and -")
    settings = settings or LoggingSettings()
    reset_logging()
    register_secret(*secret_values)

    setup = LoggingSetup()
    if console:
        console_handler = logging.StreamHandler(stream) if stream is not None else _ConsoleHandler()
        console_handler.setLevel((console_level or settings.console_level).upper())
        console_handler.setFormatter(TextFormatter(_redactor))
        setup.handlers.append(console_handler)
    if logs_dir is not None:
        Path(logs_dir).mkdir(parents=True, exist_ok=True)
        setup.json_file = Path(logs_dir) / f"reel-{role}.jsonl"
        setup.json_handler = logging.handlers.RotatingFileHandler(
            setup.json_file,
            maxBytes=int(settings.file_max_mb * 1024 * 1024),
            backupCount=settings.file_backups,
            encoding="utf-8",
            delay=True,
        )
        setup.json_handler.setLevel(settings.level)
        setup.json_handler.setFormatter(JsonFormatter(_redactor))
        setup.handlers.append(setup.json_handler)
    if jobs_dir is not None and settings.job_logs:
        job_handler = JobFileHandler(jobs_dir)
        job_handler.setLevel(settings.level)
        job_handler.setFormatter(TextFormatter(_redactor, with_date=True, with_job=False))
        setup.handlers.append(job_handler)

    root = logging.getLogger()
    _original_root_level = root.level
    context_filter = ContextFilter()
    for handler in setup.handlers:
        handler.addFilter(context_filter)
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    root.setLevel(min((handler.level for handler in setup.handlers), default=logging.WARNING))
    for name in settings.quiet_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.captureWarnings(True)
    return setup


def configure_from_config(
    cfg: LoadedConfig,
    *,
    role: str,
    console: bool = True,
    console_level: str | None = None,
    stream: TextIO | None = None,
) -> LoggingSetup:
    """Configure logging from the loaded configuration; its secret values are masked."""
    values: list[str] = []
    for name in cfg.secrets.names_set():
        if name.endswith("_FILE"):
            continue  # a path to a secret file, not a secret itself
        value = cfg.secrets.get(name) or ""
        values.append(value)
        values.extend(part.strip() for part in value.split(","))  # e.g. REEL_API_TOKENS
    return configure_logging(
        cfg.settings.logging,
        logs_dir=cfg.paths.logs_dir,
        jobs_dir=cfg.paths.data_dir / "jobs",
        role=role,
        secret_values=values,
        console=console,
        console_level=console_level,
        stream=stream,
    )


def reset_logging(*, forget_secrets: bool = False) -> None:
    """Remove the handlers installed by ``configure_logging`` (and optionally the secrets)."""
    global _original_root_level
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _MARK, False):
            root.removeHandler(handler)
            handler.close()
    if _original_root_level is not None:
        root.setLevel(_original_root_level)
        _original_root_level = None
    logging.captureWarnings(False)
    if forget_secrets:
        _redactor.clear()
