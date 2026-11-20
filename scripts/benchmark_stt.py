"""Speed and quality benchmark of Whisper models on one audio file (T-303).

Usage:
  .venv\\Scripts\\python scripts\\benchmark_stt.py samples\\bench\\excerpt.wav \\
      --models small medium large-v3-turbo large-v3 --reference large-v3 --out samples\\bench

For each model: load time, transcription time, × real time, segments, words, the share of
Armenian letters, the mean log-probability, segments dropped as repeats, and the word error
rate against the reference model (agreement, not accuracy: no human transcript exists).
Transcripts are written to ``<out>/<model>.json`` so they can be read and compared.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reel.config.schema import SttSettings  # noqa: E402
from reel.stt import read_json, wav_duration, write_json  # noqa: E402
from reel.stt.whisper import WhisperTranscriber, _load, cpu_threads  # noqa: E402

ARMENIAN = re.compile(r"[Ա-և]")
LETTER = re.compile(r"[^\W\d_]")
WORD = re.compile(r"[\w'-]+")


def tokens(text: str) -> list[str]:
    return WORD.findall(text.casefold())


def wer(reference: list[str], hypothesis: list[str]) -> float:
    """Word error rate (Levenshtein over words), two-row dynamic programming."""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    previous = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, 1):
        current = [i] + [0] * len(hypothesis)
        for j, hyp in enumerate(hypothesis, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ref != hyp))
        previous = current
    return previous[-1] / len(reference)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio", type=Path, help="16 kHz mono WAV")
    parser.add_argument("--models", nargs="+", default=["small", "medium", "large-v3"])
    parser.add_argument("--reference", default="large-v3")
    parser.add_argument("--threads", type=int, default=0, help="CPU threads (0 = automatic)")
    parser.add_argument("--beam", type=int, default=5, help="beam size")
    parser.add_argument("--no-fallback", action="store_true", help="no temperature fallback")
    parser.add_argument("--no-words", action="store_true", help="no word timestamps")
    parser.add_argument("--language", default="hy")
    parser.add_argument("--out", type=Path, default=ROOT / "samples" / "bench")
    parser.add_argument("--reuse", action="store_true", help="skip models already measured")
    args = parser.parse_args(argv)

    seconds = wav_duration(args.audio)
    results_file = args.out / "results.json"
    results = json.loads(results_file.read_text("utf-8")) if results_file.is_file() else {}
    for model in args.models:
        if args.reuse and model in results:
            continue
        settings = SttSettings(
            model=model,
            cpu_threads=args.threads,
            beam_size=args.beam,
            temperature_fallback=not args.no_fallback,
            word_timestamps=not args.no_words,
        )
        transcriber = WhisperTranscriber(settings, ROOT / "models")
        started = time.monotonic()
        _load(transcriber.path, settings.compute_type, cpu_threads(settings))
        load_seconds = time.monotonic() - started
        started = time.monotonic()
        result = transcriber.transcribe(args.audio, language=args.language)
        elapsed = time.monotonic() - started
        write_json(args.out / f"{model}.json", result)
        text = result.text
        letters = LETTER.findall(text)
        spoken = sum(s.duration for s in result.segments) or 1.0
        logprob = sum((s.avg_logprob or 0) * s.duration for s in result.segments) / spoken
        results[model] = {
            "load_seconds": round(load_seconds, 1),
            "seconds": round(elapsed, 1),
            "realtime_factor": round(elapsed / seconds, 3),
            "segments": len(result.segments),
            "words": len(tokens(text)),
            "armenian_letters": round(len(ARMENIAN.findall(text)) / max(len(letters), 1), 3),
            "mean_logprob": round(logprob, 3),
            "dropped_repeats": result.details["dropped_segments"],
            "threads": cpu_threads(settings),
            "beam": args.beam,
            "fallback": not args.no_fallback,
            "word_timestamps": not args.no_words,
        }
        args.out.mkdir(parents=True, exist_ok=True)
        results_file.write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(model, results[model], flush=True)

    reference_file = args.out / f"{args.reference}.json"
    if reference_file.is_file():
        reference = tokens(read_json(reference_file).text)
        for model in results:
            other = args.out / f"{model}.json"
            if other.is_file():
                results[model]["wer_vs_reference"] = round(
                    wer(reference, tokens(read_json(other).text)), 3
                )
        results_file.write_text(json.dumps(results, indent=1), encoding="utf-8")

    print(f"\naudio: {seconds / 60:.1f} min, reference: {args.reference}\n")
    print(
        "| model | load s | transcribe s | x real time | words | Armenian letters | mean logprob | "
        "repeats dropped | WER vs reference |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for model, r in results.items():
        print(
            f"| {model} | {r['load_seconds']} | {r['seconds']} | {r['realtime_factor']} | "
            f"{r['words']} | {r['armenian_letters']:.1%} | {r['mean_logprob']} | "
            f"{r['dropped_repeats']} | {r.get('wer_vs_reference', '—')} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
