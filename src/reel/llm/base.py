"""Local LLM interface (T-304; FR-19, NFR-8) and a scripted fake for tests.

Every request names a pydantic model: its JSON schema is sent to the LLM (structured output)
and the answer is validated against it, so callers only ever see typed, valid values.
"""

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError


class LLMError(Exception):
    """The LLM could not give a valid answer. ``retryable`` says if trying again may help."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    value: T
    text: str  # the raw JSON answer
    model: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    attempts: int = 1


def parse_output[T: BaseModel](text: str, schema: type[T]) -> T:
    """Validate the LLM's JSON answer; invalid answers raise a retryable ``LLMError``."""
    try:
        return schema.model_validate_json(text)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, error['loc'])) or 'answer'}: {error['msg']}"
            for error in exc.errors()[:5]
        )
        raise LLMError(f"the answer does not match {schema.__name__}: {problems}") from None


class LLMClient(ABC):
    name: str

    @abstractmethod
    def generate[T: BaseModel](
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str = "",
        check: Callable[[], None] | None = None,
    ) -> LLMResult[T]:
        """Answer ``prompt`` with a value of ``schema``. ``check()`` is called while the
        answer is being generated and raises when the work must stop."""


Answer = str | dict[str, Any] | BaseModel | Callable[[str, type[BaseModel]], Any]


class MeteredLLM(LLMClient):
    """Counts the calls, tokens and seconds of another client (T-805, D-020: local LLMs
    cost no money, so their compute time and tokens are what is tracked)."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.name = getattr(client, "name", "llm")
        self.calls = self.prompt_tokens = self.output_tokens = 0
        self.seconds = 0.0

    def generate[T: BaseModel](
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str = "",
        check: Callable[[], None] | None = None,
    ) -> LLMResult[T]:
        result = self.client.generate(prompt, schema, system=system, check=check)
        self.calls += 1
        self.prompt_tokens += result.prompt_tokens
        self.output_tokens += result.output_tokens
        self.seconds += result.seconds
        return result

    def usage(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "seconds": round(self.seconds, 2),
        }

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()


class FakeLLM(LLMClient):
    """Returns scripted answers in order (JSON text, dicts, models, or callables that get
    the prompt and the schema). Every call is recorded; answers are validated like real ones."""

    def __init__(self, answers: Iterable[Answer], *, name: str = "fake") -> None:
        self.name = name
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def generate[T: BaseModel](
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str = "",
        check: Callable[[], None] | None = None,
    ) -> LLMResult[T]:
        self.calls.append({"prompt": prompt, "system": system, "schema": schema})
        if check:
            check()
        if not self.answers:
            raise LLMError("FakeLLM has no scripted answer left", retryable=False)
        answer = self.answers.pop(0)
        if callable(answer) and not isinstance(answer, BaseModel):
            answer = answer(prompt, schema)
        if isinstance(answer, BaseModel):
            text = answer.model_dump_json()
        elif isinstance(answer, dict):
            text = json.dumps(answer, ensure_ascii=False)
        else:
            text = str(answer)
        return LLMResult(parse_output(text, schema), text, self.name, len(prompt.split()), 0)
