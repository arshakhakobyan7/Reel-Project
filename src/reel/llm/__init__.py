"""Local LLM (Ollama) with structured, validated answers (T-304)."""

from reel.llm.base import (
    FakeLLM,
    LLMClient,
    LLMError,
    LLMResult,
    MeteredLLM,
    parse_output,
)
from reel.llm.ollama import OllamaClient, OllamaServer, OnDemandLLM, make_llm, server_running

__all__ = [
    "FakeLLM",
    "LLMClient",
    "LLMError",
    "LLMResult",
    "MeteredLLM",
    "OllamaClient",
    "OllamaServer",
    "OnDemandLLM",
    "make_llm",
    "parse_output",
    "server_running",
]
