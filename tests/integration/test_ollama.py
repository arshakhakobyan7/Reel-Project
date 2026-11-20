"""Live smoke test with the project's own Ollama and the configured model (T-304).

Install first: ``.venv\\Scripts\\python scripts\\setup_ollama.py --pull <model>``.
"""

import re
import socket
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from reel.config import load_config
from reel.config.schema import LlmSettings
from reel.llm import OllamaServer, make_llm

ROOT = Path(__file__).resolve().parents[2]
OLLAMA = ROOT / "tools" / "ollama" / "ollama.exe"
MODELS = ROOT / "models"
MODEL = LlmSettings().model  # the default model
_name, _, _tag = MODEL.partition(":")
INSTALLED = (
    MODELS / "ollama" / "manifests" / "registry.ollama.ai" / "library" / _name / (_tag or "latest")
).is_file()
pytestmark = pytest.mark.skipif(
    not (OLLAMA.is_file() and INSTALLED),
    reason=f"needs tools/ollama and the model {MODEL} (scripts/setup_ollama.py --pull {MODEL})",
)


class LessonTitle(BaseModel):
    title: str = Field(min_length=3, max_length=80)
    topics: list[str] = Field(min_length=1, max_length=5)


def test_an_armenian_prompt_gives_schema_valid_armenian_json(project, write_config):
    with socket.socket() as probe:  # a free port, so a running Ollama is not disturbed
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    write_config(
        f"""
[paths]
ollama = '{OLLAMA.as_posix()}'
models_dir = '{MODELS.as_posix()}'

[llm]
base_url = "http://127.0.0.1:{port}"
model = "{MODEL}"
context_length = 2048
timeout_seconds = 600
"""
    )
    cfg = load_config(project / "config" / "reel.toml")
    with OllamaServer(cfg) as server:
        assert server.process is not None  # the project's own server was started
        result = make_llm(cfg).generate(
            "Դասի հատված. «Այսօր կխոսենք Python-ի ցուցակների մասին. append մեթոդը ավելացնում է "
            "տարր, իսկ pop-ը հեռացնում է վերջին տարրը»։ Գրիր դասի կարճ վերնագիր և թեմաները։",
            LessonTitle,
            system="Պատասխանիր միայն հայերեն։",
        )
    assert re.search(r"[Ա-և]", result.value.title), result.value
    assert result.prompt_tokens > 0 and result.output_tokens > 0
    assert (project / "data" / "ollama-home").is_dir()  # Ollama's own files stay in the project
