"""Reel in one process (T-901; NFR-2, NFR-3, UJ-4): the dashboard, the folder watcher, the
workers and the staff notifier, started and stopped together.

- On start, the leases of Reel processes on this computer that no longer run are ended, so
  a job that was in progress when the computer or the process died is picked up again at
  once from its stage (finished stages are not repeated; publications are idempotent,
  D-048).
- Exactly one staff notifier runs per database (note from T-801). It starts first, so on a
  new database it marks the existing events as seen before anything new happens. The
  workers do not notify staff themselves.
- Stopping is graceful: the dashboard stops taking requests, the watcher and the notifier
  stop at once, and the workers ask running stages to stop and wait for them
  (``workers.stop_grace_seconds``); an interrupted stage starts again next time.
"""

import ctypes
import logging
import os
import re
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass

from reel.config.loader import LoadedConfig
from reel.db import Database
from reel.queue import JobQueue, StageRegistry, Worker

log = logging.getLogger("reel.runtime")

_STILL_ACTIVE = 259
_QUERY_LIMITED = 0x1000
_ACCESS_DENIED = 5


def process_alive(pid: int) -> bool:
    """True if a process with this id runs (never stops or signals it)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(_QUERY_LIMITED, False, pid)
        if not handle:
            return ctypes.get_last_error() == _ACCESS_DENIED  # exists, owned by someone else
        try:
            code = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
                return True
            return code.value == _STILL_ACTIVE
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)  # signal 0 only checks (POSIX)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def gone_here(owner: str, *, host: str | None = None, alive=process_alive) -> bool:
    """A worker name ``<host>-<pid>-<n>`` of this computer whose process no longer runs."""
    host = host or socket.gethostname()
    match = re.fullmatch(re.escape(host) + r"-(\d+)-\d+", owner)
    if match is None:
        return False  # another computer: its lease runs out by itself
    pid = int(match[1])
    return pid != os.getpid() and not alive(pid)


@dataclass
class Parts:
    web: bool = True
    watcher: bool = True
    worker: bool = True
    notifier: bool = True
    cleanup: bool = True  # retention, once a day (T-902)


class Runtime:
    def __init__(
        self,
        db: Database,
        config: Callable[[], LoadedConfig],
        *,
        parts: Parts | None = None,
        registry: StageRegistry | None = None,
        host: str | None = None,
        port: int | None = None,
        secret: str | None = None,
        cleanup_delay: float = 600.0,
    ) -> None:
        self.db, self.config = db, config
        self.cleanup_delay = cleanup_delay
        self.parts = parts or Parts()
        self.registry = registry
        self.host, self.port, self.secret = host, port, secret
        self.stopping = threading.Event()
        self.threads: list[threading.Thread] = []
        self.worker: Worker | None = None
        self.server = None

    def _thread(self, name: str, target: Callable[[], None]) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self.threads.append(thread)

    def start(self) -> None:
        cfg = self.config()
        queue = JobQueue.from_settings(self.db, cfg.settings.workers)
        ended = queue.expire_leases_of(gone_here)
        if ended:
            log.warning("jobs %s were in progress in a Reel process that no longer runs; "
                        "they continue from their stage", ", ".join(map(str, ended)))  # fmt: skip
        if self.parts.notifier:
            self._start_notifier(cfg)
        if self.parts.watcher:
            from reel.ingest.watcher import FolderWatcher

            watcher = FolderWatcher(self.db, self.config)
            self._thread("watcher", lambda: watcher.run(self.stopping))
        if self.parts.worker:
            from reel import stages

            registry = self.registry or stages.default_registry()
            self.worker = Worker(self.db, registry, self.config)
            self.worker.start()
        if self.parts.cleanup:
            from reel.retention import daily

            def cleanup() -> None:
                for _ in daily(self.db, self.config, self.stopping, first_delay=self.cleanup_delay):
                    pass

            self._thread("cleanup", cleanup)
        if self.parts.web:
            self._start_web(cfg)
        log.info("Reel started: %s", ", ".join(
            name for name, on in vars(self.parts).items() if on) or "nothing")  # fmt: skip

    def _start_notifier(self, cfg: LoadedConfig) -> None:
        from reel.notify import StaffNotifier

        try:
            notifier = StaffNotifier(self.db, cfg)
            notifier.run_once()  # first: on a new database, existing events are only marked
        except Exception:
            log.exception("staff notifications are off: the notifier could not start")
            return
        self._thread("notifier", lambda: notifier.run(self.stopping))

    def _start_web(self, cfg: LoadedConfig) -> None:
        import uvicorn

        from reel.web.app import create_app
        from reel.web.commands import session_secret

        app = create_app(self.db, self.config, self.secret or session_secret(cfg))
        web = cfg.settings.web
        self.server = uvicorn.Server(
            uvicorn.Config(
                app, host=self.host or web.host, port=self.port or web.port, log_config=None,
                server_header=False, date_header=False, lifespan="off",
            )
        )  # fmt: skip
        self._thread("web", self.server.run)  # not the main thread: uvicorn leaves signals alone

    @property
    def web_started(self) -> bool:
        return bool(self.server is not None and self.server.started)

    def alive(self) -> bool:
        """False when a part stopped by itself (e.g. the web port was taken)."""
        if self.worker is not None and not self.worker.alive:
            return False
        return all(thread.is_alive() for thread in self.threads)

    def stop(self, timeout: float | None = None) -> None:
        """Stop every part; running stages get ``workers.stop_grace_seconds`` to stop."""
        self.stopping.set()
        if self.server is not None:
            self.server.should_exit = True
        if self.worker is not None:
            self.worker.request_stop()
            self.worker.stop(timeout)
        for thread in self.threads:
            thread.join(timeout if timeout is not None else 30)
        log.info("Reel stopped")
