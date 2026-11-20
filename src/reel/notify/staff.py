"""Consume job events into short Armenian staff alerts (T-801).

One active consumer process per database is required. Instances/threads in this process
are serialised. A pending payload plus Slack metadata recovery handles a restart after
posting but before acknowledging. Event-time flags are preferred when in event.data;
legacy events without a flags snapshot use the job's flags at first outbox observation.
"""

import json
import logging
import math
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select

from reel.config.loader import LoadedConfig
from reel.db import AppMeta, Database, EventKind, JobEvent, Stage, utcnow
from reel.logging_setup import Redactor
from reel.metrics import BUDGET_EVENT
from reel.publish.slack import (
    SlackClient,
    SlackError,
    SlackPost,
    _external_id,
    _validate_channel,
    render_message,
)

# After the upload, only these flags need staff at once (R-5: YouTube locked the video
# to private, T-706).
URGENT_UPLOAD_FLAGS = frozenset({"youtube_not_unlisted"})

log = logging.getLogger("reel.notify")
WATERMARK = "notifications.last_event_id"
PENDING = "notifications.pending_event"
_LOCK_GUARD = threading.Lock()
_RUN_LOCKS: dict[str, Any] = {}


@dataclass(frozen=True)
class _Alert:
    event_id: int
    job_id: int
    stage: str
    kind: str
    text: str
    channel: str
    mode: str
    instance_id: str

    def metadata(self) -> dict[str, Any]:
        return {
            "event_type": "reel_staff_alert",
            "event_payload": {
                "job_id": self.job_id,
                "event_id": self.event_id,
                "instance_id": self.instance_id,
            },
        }

    @classmethod
    def load(cls, value: str) -> _Alert:
        try:
            data = json.loads(value)
            if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
                raise ValueError("invalid fields")
            if any(type(data[key]) is not int or data[key] < 1 for key in ("event_id", "job_id")):
                raise ValueError("invalid ids")
            if any(
                not isinstance(data[key], str)
                for key in ("stage", "kind", "text", "channel", "mode", "instance_id")
            ) or data["mode"] not in {"live", "dry-run"}:
                raise ValueError("invalid payload")
            if data["kind"] not in {"failed", "on_hold", "flagged"}:
                raise ValueError("invalid notification kind")
            return cls(**data)
        except ValueError, TypeError, RecursionError:
            raise RuntimeError("invalid notifications.pending_event; repair outbox state") from None


def _watermark(meta: AppMeta) -> int:
    try:
        value = int(meta.value)
        if not 0 <= value <= 2**63 - 1:
            raise ValueError("invalid id")
        return value
    except ValueError:
        raise RuntimeError("invalid notifications.last_event_id; repair outbox state") from None


class StaffNotifier:
    """Poll committed events, acknowledge each delivered/skipped event in a short transaction.

    run_once returns relevant events completed, including dry-run logs and recovered
    deliveries. It returns zero when bootstrapping, idle, or only skipping events.
    The worker Attention callback is not another delivery source; events are authoritative.
    """

    def __init__(
        self,
        db: Database,
        cfg: LoadedConfig,
        client: SlackClient | None = None,
        clock: Callable[[], datetime] = utcnow,
        *,
        poll_interval: float = 30.0,
        history_page_size: int = 100,
        max_history_pages: int = 100,
    ) -> None:
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be finite and positive")
        if not 1 <= history_page_size <= 200 or max_history_pages < 1:
            raise ValueError("invalid notification history limits")
        base = cfg.settings.web.base_url.rstrip("/")
        parsed = urlsplit(base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(ord(char) <= 32 for char in base)
        ):
            raise ValueError("invalid notification dashboard base_url")
        self.db, self.cfg, self.clock = db, cfg, clock
        self.poll_interval = poll_interval
        self._base_url = base
        self._page_size, self._max_pages = history_page_size, max_history_pages
        token = cfg.secrets.get("SLACK_BOT_TOKEN")
        if cfg.settings.notifications.mode == "live":
            _validate_channel(cfg.settings.notifications.slack_channel)
            if (
                not token
                or not token.isascii()
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in token)
            ):
                raise SlackError("missing_token", retryable=False)
        self.client = client if client is not None else SlackClient(token)
        self._secrets = sorted(
            (cfg.secrets.get(name) for name in cfg.secrets.names_set()), key=len, reverse=True
        )
        self._redactor = Redactor()
        self._redactor.add(*self._secrets)
        with _LOCK_GUARD:
            self._lock = _RUN_LOCKS.setdefault(str(db.path), threading.Lock())

    def _safe(self, value: Any, maximum: int = 600) -> str:
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        text = self._redactor.redact(text)
        return text if len(text) <= maximum else text[:maximum] + "…"

    def _alert(self, event: JobEvent, instance_id: str) -> _Alert | None:
        job, data = event.job, event.data or {}
        stage = event.stage or job.stage
        if event.kind == EventKind.FAILED:
            kind, heading = "failed", "❌ Առաջադրանքը ձախողվել է"
        elif event.kind == EventKind.HELD:
            kind, heading = "on_hold", "⏸ Առաջադրանքը սպասում է աշխատակցի որոշմանը"
        elif event.kind == BUDGET_EVENT:
            kind, heading = "flagged", "⏱ Մշակումը գերազանցեց ժամանակի բյուջեն"
        elif event.kind == EventKind.STAGE_COMPLETED and (
            stage in {Stage.IMPORT, Stage.TRANSCRIBE}
            or (stage is Stage.UPLOAD and URGENT_UPLOAD_FLAGS & set(data.get("flags") or ()))
        ):
            kind, heading = "flagged", "⚠️ Առաջադրանքը ստուգման կարիք ունի"
        else:
            return None
        if kind not in self.cfg.settings.notifications.events:
            return None
        flags = data.get("flags", job.flags)
        if kind == "flagged" and not flags:
            return None
        recording = job.recording
        name = self._safe(PureWindowsPath(recording.source_path).name, 160)
        group = self._safe(recording.group_name, 160) if recording.group_name else ""
        lesson = recording.lesson_number
        identity = (
            f"{group or name} · Դաս {lesson}"
            if lesson is not None
            else f"{group} · {name}"
            if group
            else name
        )
        lines = [f"Փուլ՝ {stage.label}"]
        if kind == "failed":
            lines.append("Փորձեր՝ " + self._safe(data.get("attempt", job.attempts), 30))
            lines.append("Սխալ՝ " + self._safe(event.message or job.last_error or "Անհայտ սխալ"))
        elif kind == "on_hold":
            lines.append("Պատճառ՝ " + self._safe(data.get("reason") or job.hold_reason or "Անհայտ"))
            lines.append(self._safe(event.message or job.hold_message or ""))
        else:
            lines.append(
                "Նշումներ՝ "
                + self._safe(
                    ", ".join(str(flag) for flag in flags)
                    if isinstance(flags, (tuple, list))
                    else flags
                )
            )
        if event.kind == BUDGET_EVENT:
            lines.append(self._safe(event.message))
        channel = self.cfg.settings.notifications.slack_channel
        post = SlackPost(
            job_id=job.id,
            channel=channel,
            title=heading,
            group=identity,
            summary="\n".join(lines),
            url=f"{self._base_url}/jobs/{job.id}",
        )
        text = render_message("{title}\n{group}\n{summary}\n{url}", post)
        return _Alert(
            event.id,
            job.id,
            str(stage),
            kind,
            text,
            channel,
            self.cfg.settings.notifications.mode,
            instance_id,
        )

    def _prepare(self) -> tuple[_Alert | None, bool]:
        """Return an immutable pending alert or indicate whether scanning should continue."""
        with self.db.transaction(immediate=True) as session:
            meta = session.get(AppMeta, WATERMARK)
            pending = session.get(AppMeta, PENDING)
            if meta is None:
                if pending is not None:
                    raise RuntimeError("notification pending state exists without a watermark")
                newest = session.scalar(select(func.max(JobEvent.id))) or 0
                session.add(AppMeta(key=WATERMARK, value=str(newest)))
                return None, False
            after = _watermark(meta)
            if pending is not None:
                alert = _Alert.load(pending.value)
                if alert.event_id <= after:
                    session.delete(pending)
                    return None, True
                if alert.kind not in self.cfg.settings.notifications.events:
                    meta.value = str(alert.event_id)
                    session.delete(pending)
                    return None, True
                return alert, True
            event = session.scalar(
                select(JobEvent).where(JobEvent.id > after).order_by(JobEvent.id).limit(1)
            )
            if event is None:
                return None, False
            instance = session.get(AppMeta, "instance_id")
            if instance is None or not instance.value:
                raise RuntimeError("staff notifications need the database instance_id")
            alert = self._alert(event, instance.value)
            if alert is None:
                meta.value = str(event.id)
                return None, True
            session.add(AppMeta(key=PENDING, value=json.dumps(asdict(alert), ensure_ascii=False)))
            return alert, True

    def _ack(self, alert: _Alert) -> None:
        with self.db.transaction(immediate=True) as session:
            meta, pending = session.get(AppMeta, WATERMARK), session.get(AppMeta, PENDING)
            if (
                meta is None
                or pending is None
                or _Alert.load(pending.value).event_id != alert.event_id
            ):
                raise RuntimeError("notification outbox changed before acknowledgement")
            meta.value = str(max(_watermark(meta), alert.event_id))
            session.delete(pending)

    def _exists(self, alert: _Alert) -> bool:
        """Recover a committed Slack post whose watermark acknowledgement was lost."""
        cursor, seen = "", set()
        expected = alert.metadata()["event_payload"]
        for _ in range(self._max_pages):
            page = self.client.history(alert.channel, cursor=cursor, limit=self._page_size)
            if not isinstance(page, dict) or not isinstance(page.get("messages"), list):
                raise SlackError("invalid_response", retryable=True)
            for message in page["messages"]:
                if not isinstance(message, dict):
                    raise SlackError("invalid_response", retryable=True)
                metadata = message.get("metadata")
                if metadata is None:
                    continue
                if not isinstance(metadata, dict):
                    raise SlackError("invalid_response", retryable=True)
                if metadata.get("event_type") != "reel_staff_alert":
                    continue
                payload = metadata.get("event_payload")
                if not isinstance(payload, dict):
                    raise SlackError("invalid_response", retryable=True)
                matches = (
                    all(
                        type(payload.get(key)) in {str, int}
                        and str(payload[key]) == str(expected[key])
                        for key in ("job_id", "event_id")
                    )
                    and payload.get("instance_id") == alert.instance_id
                )
                if matches:
                    _external_id(alert.channel, message.get("ts"))
                    return True
            response_metadata = page.get("response_metadata", {})
            if (
                not isinstance(response_metadata, dict)
                or type(page.get("has_more", False)) is not bool
            ):
                raise SlackError("invalid_response", retryable=True)
            next_cursor = response_metadata.get("next_cursor", "")
            if not isinstance(next_cursor, str):
                raise SlackError("invalid_response", retryable=True)
            next_cursor = next_cursor.strip()
            if not next_cursor:
                if page.get("has_more", False):
                    raise SlackError("history_incomplete", retryable=True)
                return False
            if next_cursor in seen:
                raise SlackError("history_incomplete", retryable=True)
            seen.add(next_cursor)
            cursor = next_cursor
        raise SlackError("history_incomplete", retryable=True)

    def run_once(self) -> int:
        """Stop at a retryable error; skip permanent errors without blocking later events."""
        with self._lock:
            completed = 0
            while True:
                alert, more = self._prepare()
                if alert is None:
                    if more:
                        continue
                    return completed
                dry_run = (
                    self.cfg.settings.notifications.mode == "dry-run" or alert.mode == "dry-run"
                )
                try:
                    recovered = False
                    if not dry_run:
                        _validate_channel(alert.channel)
                        recovered = self._exists(alert)
                        if not recovered:
                            response = self.client.post_message(
                                alert.channel, alert.text, alert.metadata()
                            )
                            if (
                                not isinstance(response, dict)
                                or response.get("ok") is not True
                                or response.get("channel") != alert.channel
                            ):
                                raise SlackError("invalid_response", retryable=True)
                            _external_id(alert.channel, response.get("ts"))
                except SlackError as error:
                    log.log(
                        logging.WARNING if error.retryable else logging.ERROR,
                        "Staff alert event %s %s: %s",
                        alert.event_id,
                        "will retry" if error.retryable else "skipped",
                        self._safe(str(error), 200),
                    )
                    if error.retryable:
                        return completed
                    self._ack(alert)
                    continue
                log.info(
                    "Staff alert%s event %s: %s",
                    " (dry-run)" if dry_run else " (recovered)" if recovered else "",
                    alert.event_id,
                    self._safe(alert.text, 3500),
                    extra={
                        "job_id": alert.job_id,
                        "event_id": alert.event_id,
                        "stage": alert.stage,
                        "notification_at": self.clock().isoformat(),
                    },
                )
                self._ack(alert)
                completed += 1

    def run(self, stop: threading.Event) -> None:
        """Poll until stopped; Event.wait lets shutdown interrupt the interval immediately."""
        while not stop.is_set():
            try:
                self.run_once()
            except Exception as error:
                log.error(
                    "Staff notifier run failed (%s); pending events are retained",
                    type(error).__name__,
                )
            stop.wait(self.poll_interval)
