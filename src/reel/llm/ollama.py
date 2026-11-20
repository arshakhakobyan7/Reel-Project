"""Ollama client and local server (T-304; D-004, NFR-8).

``OllamaClient`` uses the chat API with streaming (``check()`` runs between chunks, and a
long answer never hits the read timeout while tokens keep coming) and with the answer's JSON
schema as ``format`` (structured output). Timeouts, connection errors, server errors and
invalid answers are retried ``llm.max_retries`` times with a short backoff. Token counts and
time are returned with every result.

``OllamaServer`` starts the project's own ``tools/ollama/ollama.exe serve`` when nothing
answers at a local ``base_url``. Models stay in ``models/ollama`` and Ollama's own files (its
key) in ``data/ollama-home``, so nothing is written to the user profile.
"""

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import BaseModel

from reel.config.loader import LoadedConfig
from reel.config.schema import LlmSettings
from reel.llm.base import LLMClient, LLMError, LLMResult, parse_output

log = logging.getLogger("reel.llm")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class OllamaClient(LLMClient):
    def __init__(
        self,
        settings: LlmSettings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        backoff_seconds: float = 2.0,
    ) -> None:
        self.settings = settings
        self.name = f"ollama {settings.model}"
        self._sleep = sleep
        self._backoff = backoff_seconds
        self._url = settings.base_url.rstrip("/") + "/api/chat"

    def generate[T: BaseModel](
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str = "",
        check: Callable[[], None] | None = None,
    ) -> LLMResult[T]:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        body = {
            "model": self.settings.model,
            "messages": messages,
            "format": schema.model_json_schema(),
            "stream": True,
            "think": False,
            "options": {
                "temperature": self.settings.temperature,
                "num_ctx": self.settings.context_length,
            },
        }
        attempts = self.settings.max_retries + 1
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                text, final = self._chat(body, check)
                value = parse_output(text, schema)
            except LLMError as exc:
                if not exc.retryable or attempt == attempts:
                    raise LLMError(
                        f"{exc} (after {attempt} attempt(s))", retryable=exc.retryable
                    ) from None
                log.warning("LLM attempt %d of %d failed: %s; trying again", attempt, attempts, exc)
                self._sleep(self._backoff * attempt)
                continue
            return LLMResult(
                value,
                text,
                self.settings.model,
                int(final.get("prompt_eval_count") or 0),
                int(final.get("eval_count") or 0),
                round(time.monotonic() - started, 3),
                attempt,
            )
        raise AssertionError("unreachable")

    def _chat(
        self, body: dict[str, Any], check: Callable[[], None] | None
    ) -> tuple[str, dict[str, Any]]:
        request = urllib.request.Request(
            self._url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        pieces: list[str] = []
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                for line in response:
                    if check:
                        check()
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if "error" in event:
                        raise LLMError(f"Ollama: {event['error']}")
                    pieces.append((event.get("message") or {}).get("content") or "")
                    if event.get("done"):
                        return "".join(pieces), event
        except urllib.error.HTTPError as exc:
            with exc:
                detail = _error_text(exc)
            if exc.code == 404:
                model = self.settings.model
                raise LLMError(
                    f"model {model!r} is not installed: run scripts/setup_ollama.py --pull {model}"
                    f" ({detail})",
                    retryable=False,
                ) from None
            raise LLMError(
                f"Ollama returned HTTP {exc.code}: {detail}", retryable=exc.code >= 500
            ) from None
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise LLMError(f"Ollama is not reachable at {self.settings.base_url}: {exc}") from None
        except json.JSONDecodeError:
            raise LLMError("Ollama sent a response that is not JSON") from None
        raise LLMError("the answer ended before Ollama reported it complete")


def _error_text(exc: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(exc.read(4096)).get("error") or exc.reason)
    except ValueError, OSError:
        return str(exc.reason)


def make_llm(cfg: LoadedConfig) -> LLMClient:
    """The LLM chosen in ``[llm]`` (only Ollama so far)."""
    return OllamaClient(cfg.settings.llm)


def server_running(base_url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/version", timeout=timeout):
            return True
    except OSError, ValueError:
        return False


class OllamaServer:
    """Use the Ollama server at ``[llm] base_url``; start the project's own if none answers."""

    def __init__(self, cfg: LoadedConfig, *, start_timeout: float = 60.0) -> None:
        self.base_url = cfg.settings.llm.base_url.rstrip("/")
        self.executable = Path(cfg.paths.ollama)
        self.models_dir = cfg.paths.models_dir / "ollama"
        self.home = cfg.paths.data_dir / "ollama-home"
        self.log_file = cfg.paths.logs_dir / "ollama.log"
        self.use_igpu = cfg.settings.llm.use_igpu
        self.start_timeout = start_timeout
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> Self:
        if server_running(self.base_url):
            log.info("using the Ollama server at %s", self.base_url)
            return self
        parts = urlsplit(self.base_url)
        if parts.hostname not in {"127.0.0.1", "localhost"}:
            raise LLMError(f"no Ollama server answers at {self.base_url}", retryable=True)
        if not self.executable.is_file():
            raise LLMError(
                f"Ollama is not installed at {self.executable}: run scripts/setup_ollama.py",
                retryable=False,
            )
        for folder in (self.models_dir, self.home, self.log_file.parent):
            folder.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "OLLAMA_HOST": f"{parts.hostname}:{parts.port or 11434}",
            "OLLAMA_MODELS": str(self.models_dir),
            "USERPROFILE": str(self.home),  # Ollama keeps its key in ~/.ollama
            "HOME": str(self.home),
        }
        if self.use_igpu:
            env["OLLAMA_IGPU_ENABLE"] = "1"
        with self.log_file.open("ab") as output:
            self.process = subprocess.Popen(
                [str(self.executable), "serve"],
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=NO_WINDOW,
            )
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if server_running(self.base_url, timeout=1.0):
                log.info("started Ollama (%s) at %s", self.executable, self.base_url)
                return self
            if self.process.poll() is not None:
                break
            time.sleep(0.25)
        self.stop()
        raise LLMError(f"Ollama did not start; see {self.log_file}", retryable=True)

    def stop(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()


class OnDemandLLM(LLMClient):
    """Starts the project's Ollama only when a question is really asked (it needs ~4 GB)."""

    name = "ollama (on demand)"

    def __init__(self, cfg: LoadedConfig) -> None:
        self.cfg = cfg
        self.server: OllamaServer | None = None
        self.client: LLMClient | None = None

    def generate(self, prompt, schema, *, system="", check=None):  # type: ignore[override]
        if self.client is None:
            self.server = OllamaServer(self.cfg).start()
            self.client = make_llm(self.cfg)
        return self.client.generate(prompt, schema, system=system, check=check)

    def close(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None
