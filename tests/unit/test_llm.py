"""LLM interface, Ollama client against a local mock server, FakeLLM (T-304)."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import BaseModel, Field

from reel.config import load_config
from reel.config.schema import LlmSettings
from reel.llm import FakeLLM, LLMError, OllamaClient, OllamaServer, server_running


class Chapter(BaseModel):
    start_seconds: float = Field(ge=0)
    title: str = Field(min_length=1)


class Outline(BaseModel):
    title: str = Field(min_length=1)
    chapters: list[Chapter]


GOOD = {"title": "Ցուցակի մեթոդներ", "chapters": [{"start_seconds": 0, "title": "append և pop"}]}


class MockOllama:
    """``/api/chat`` with scripted behaviour per request; ``/api/version`` always answers."""

    def __init__(self) -> None:
        self.script: list[tuple[str, object]] = []
        self.bodies: list[dict] = []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._json(200, {"version": "0.40.16"})

            def do_POST(self):
                mock.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                kind, value = mock.script.pop(0) if mock.script else ("answer", GOOD)
                if kind == "status":
                    self._json(value, {"error": f"status {value}"})
                    return
                if kind == "delay":
                    time.sleep(value)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                if kind == "error_event":
                    self._line({"error": value})
                    return
                for i in range(0, len(text), 7):
                    self._line({"message": {"role": "assistant", "content": text[i : i + 7]}})
                if kind != "truncated":
                    self._line({"done": True, "prompt_eval_count": 120, "eval_count": 30})

            def _line(self, event):
                try:
                    self.wfile.write(json.dumps(event, ensure_ascii=False).encode() + b"\n")
                    self.wfile.flush()
                except ConnectionError:  # the client gave up (timeout tests)
                    pass

            def _json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.fixture
def ollama():
    with MockOllama() as mock:
        yield mock


def client(url, **settings):
    sleeps = []
    values = {"base_url": url, "model": "gemma3:4b", "timeout_seconds": 5, **settings}
    return OllamaClient(LlmSettings(**values), sleep=sleeps.append), sleeps


def test_a_structured_answer_with_token_accounting(ollama):
    llm, sleeps = client(ollama.url, temperature=0.1, context_length=4096)
    result = llm.generate("Գրիր վերնագիր", Outline, system="Դու օգնական ես")
    assert result.value == Outline.model_validate(GOOD)
    assert (result.prompt_tokens, result.output_tokens, result.attempts) == (120, 30, 1)
    assert result.model == "gemma3:4b" and result.seconds >= 0 and sleeps == []
    (body,) = ollama.bodies
    assert body["format"] == Outline.model_json_schema()
    assert body["stream"] is True and body["think"] is False
    assert body["options"] == {"temperature": 0.1, "num_ctx": 4096}
    assert body["messages"] == [
        {"role": "system", "content": "Դու օգնական ես"},
        {"role": "user", "content": "Գրիր վերնագիր"},
    ]


def test_invalid_json_is_retried(ollama):
    ollama.script = [("answer", "{not json"), ("answer", GOOD)]
    llm, sleeps = client(ollama.url)
    result = llm.generate("x", Outline)
    assert result.attempts == 2 and len(sleeps) == 1


def test_an_answer_that_never_matches_the_schema_fails_after_the_retries(ollama):
    ollama.script = [("answer", {"title": ""})] * 3
    llm, sleeps = client(ollama.url, max_retries=2)
    with pytest.raises(LLMError, match=r"does not match Outline.*after 3 attempt") as error:
        llm.generate("x", Outline)
    assert error.value.retryable and len(ollama.bodies) == 3 and len(sleeps) == 2


def test_a_timeout_is_retried_then_reported(ollama):
    ollama.script = [("delay", 1.0)] * 2
    llm, _ = client(ollama.url, timeout_seconds=0.2, max_retries=1)
    started = time.monotonic()
    with pytest.raises(LLMError, match="not reachable.*after 2 attempt"):
        llm.generate("x", Outline)
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ([("status", 500), ("answer", GOOD)], None),
        ([("error_event", "out of memory"), ("answer", GOOD)], None),
        ([("truncated", GOOD), ("answer", GOOD)], None),
    ],
)
def test_server_errors_are_retried(ollama, script, message):
    ollama.script = list(script)
    llm, _ = client(ollama.url)
    assert llm.generate("x", Outline).attempts == 2


def test_a_missing_model_is_not_retried(ollama):
    ollama.script = [("status", 404)]
    llm, sleeps = client(ollama.url)
    with pytest.raises(LLMError, match="setup_ollama.py --pull gemma3:4b") as error:
        llm.generate("x", Outline)
    assert not error.value.retryable and sleeps == [] and len(ollama.bodies) == 1


def test_no_server_is_a_retryable_error():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    llm, _ = client(f"http://127.0.0.1:{port}", max_retries=0)
    with pytest.raises(LLMError, match="not reachable") as error:
        llm.generate("x", Outline)
    assert error.value.retryable


def test_check_stops_the_answer(ollama):
    llm, _ = client(ollama.url)

    def stop():
        raise RuntimeError("stage cancelled")

    with pytest.raises(RuntimeError, match="stage cancelled"):
        llm.generate("x", Outline, check=stop)


def test_fake_llm():
    fake = FakeLLM([GOOD, Outline.model_validate(GOOD), json.dumps(GOOD), lambda p, s: GOOD, "{}"])
    for _ in range(4):
        assert fake.generate("Բարև", Outline).value.title == "Ցուցակի մեթոդներ"
    with pytest.raises(LLMError, match="does not match"):
        fake.generate("x", Outline)
    with pytest.raises(LLMError, match="no scripted answer"):
        fake.generate("x", Outline)
    assert fake.calls[0] == {"prompt": "Բարև", "system": "", "schema": Outline}


def test_an_answering_server_is_used_as_it_is(ollama, project, write_config):
    write_config(f'[llm]\nbase_url = "{ollama.url}"\n')
    cfg = load_config(project / "config" / "reel.toml")
    assert server_running(ollama.url)
    with OllamaServer(cfg) as server:
        assert server.process is None  # nothing was started


def test_the_server_cannot_start_without_ollama(project, write_config):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    write_config(f'[llm]\nbase_url = "http://127.0.0.1:{port}"\n[paths]\nollama = "missing.exe"\n')
    cfg = load_config(project / "config" / "reel.toml")
    with pytest.raises(LLMError, match="Ollama is not installed") as error:
        OllamaServer(cfg).start()
    assert not error.value.retryable


@pytest.mark.parametrize("use_igpu", [True, False])
def test_the_projects_own_server_keeps_its_files_in_the_project(
    project, write_config, monkeypatch, use_igpu
):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    exe = project / "tools" / "ollama.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    write_config(
        f'[llm]\nbase_url = "http://127.0.0.1:{port}"\nuse_igpu = {str(use_igpu).lower()}\n'
        f"[paths]\nollama = '{exe.as_posix()}'\n"
    )
    cfg = load_config(project / "config" / "reel.toml")
    started = {}

    class FakeProcess:
        def __init__(self, command, *, env, **kwargs):
            started.update(command=command, env=env)

        def poll(self):
            return None

        def terminate(self):
            started["stopped"] = True

        def wait(self, timeout=None):
            return 0

    answers = iter([False, True])  # not running yet, then up
    monkeypatch.setattr("reel.llm.ollama.server_running", lambda *a, **k: next(answers))
    monkeypatch.setattr("reel.llm.ollama.subprocess.Popen", FakeProcess)
    with OllamaServer(cfg) as server:
        assert server.process is not None
    env = started["env"]
    assert started["command"] == [str(exe), "serve"] and started["stopped"]
    assert env["OLLAMA_HOST"] == f"127.0.0.1:{port}"
    assert env["OLLAMA_MODELS"] == str(cfg.paths.models_dir / "ollama")
    assert env["USERPROFILE"] == str(cfg.paths.data_dir / "ollama-home")
    assert ("OLLAMA_IGPU_ENABLE" in env) is use_igpu
