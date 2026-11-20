"""Small Armenian test of local LLMs for Reel's tasks (T-304).

Usage:
  .venv\\Scripts\\python scripts\\benchmark_llm.py \\
      samples\\bench\\final-nowords\\large-v3-turbo.json --models gemma3:4b qwen3:8b
      --out samples\\bench\\llm

Gives each model a real (machine-transcribed) lesson excerpt and asks, with structured
output, for an Armenian title, a two-sentence summary and chapter titles with start times.
Prints time, tokens per second and the answers (written to ``<out>/<model>.json``) so the
Armenian quality can be read and compared.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import BaseModel, Field  # noqa: E402

from reel.config import load_config  # noqa: E402
from reel.llm import LLMError, OllamaClient, OllamaServer  # noqa: E402
from reel.stt import read_json  # noqa: E402

ARMENIAN = re.compile(r"[Ա-և]")
SYSTEM = (
    "Դու օգնում ես ծրագրավորման դասընթացի դասախոսներին։ Տեքստը դասի ավտոմատ "
    "ձայնագրությունն է և կարող է սխալներ ունենալ։ Պատասխանիր միայն հայերեն։"
)
PROMPT = """Ահա դասի հատվածը (ժամանակը վայրկյաններով).

{lines}

Գրիր.
- title: դասի այս հատվածի կարճ վերնագիր (մինչև 60 նիշ);
- summary: երկու նախադասությամբ ամփոփում;
- chapters: 3-5 գլուխ, յուրաքանչյուրը՝ start_seconds (վերևի ժամանակներից) և title (մինչև 60 նիշ)։"""


class Chapter(BaseModel):
    start_seconds: float = Field(ge=0)
    title: str = Field(min_length=3, max_length=80)


class Outline(BaseModel):
    title: str = Field(min_length=3, max_length=80)
    summary: str = Field(min_length=10, max_length=600)
    chapters: list[Chapter] = Field(min_length=3, max_length=6)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--models", nargs="+", default=["gemma3:4b"])
    parser.add_argument("--out", type=Path, default=ROOT / "samples" / "bench" / "llm")
    args = parser.parse_args(argv)

    transcript = read_json(args.transcript)
    lines = "\n".join(f"[{s.start:.0f}] {s.text}" for s in transcript.segments)
    prompt = PROMPT.format(lines=lines)
    cfg = load_config(ROOT / "config" / "reel.toml")
    args.out.mkdir(parents=True, exist_ok=True)
    with OllamaServer(cfg):
        for model in args.models:
            settings = cfg.settings.llm.model_copy(update={"model": model, "max_retries": 1})
            started = time.monotonic()
            try:
                result = OllamaClient(settings).generate(prompt, Outline, system=SYSTEM)
            except LLMError as exc:
                print(f"{model}: FAILED {exc}")
                continue
            seconds = time.monotonic() - started
            answer = result.value.model_dump()
            text = json.dumps(answer, ensure_ascii=False)
            letters = re.findall(r"[^\W\d_]", text)
            summary = {
                "model": model,
                "seconds": round(seconds, 1),
                "prompt_tokens": result.prompt_tokens,
                "output_tokens": result.output_tokens,
                "output_tokens_per_second": round(result.output_tokens / seconds, 2),
                "attempts": result.attempts,
                "armenian_letters": round(len(ARMENIAN.findall(text)) / max(len(letters), 1), 3),
            }
            (args.out / f"{model.replace(':', '_')}.json").write_text(
                json.dumps({**summary, "answer": answer}, ensure_ascii=False, indent=1), "utf-8"
            )
            print(json.dumps(summary, ensure_ascii=False))
            print(json.dumps(answer, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
