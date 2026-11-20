"""Download a faster-whisper speech model into models/whisper/<model> (T-302).

Usage:  .venv\\Scripts\\python scripts\\setup_whisper.py --model large-v3
        .venv\\Scripts\\python scripts\\setup_whisper.py --model tiny      (small test model)

The models are the free Systran conversions of OpenAI Whisper (MIT) from Hugging Face. The
download cache is kept inside the project as well, so nothing is written to the user profile.
Running it again only checks what is already there.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def folder_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="large-v3", help="model name, e.g. tiny, large-v3")
    parser.add_argument("--models-dir", default=str(ROOT / "models"), help="default: models/")
    args = parser.parse_args(argv)

    whisper_dir = Path(args.models_dir) / "whisper"
    os.environ.setdefault("HF_HOME", str(whisper_dir / ".hf-cache"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    sys.path.insert(0, str(ROOT / "src"))
    from faster_whisper import WhisperModel, download_model

    from reel.stt.whisper import model_dir, model_installed

    target = model_dir(Path(args.models_dir), args.model)
    if model_installed(target):
        print(f"already installed: {target}")
    else:
        print(f"downloading {args.model} into {target} ...")
        download_model(args.model, output_dir=str(target), cache_dir=os.environ["HF_HOME"])
        if not model_installed(target):
            print(f"ERROR: the download is incomplete: {target}", file=sys.stderr)
            return 1
    WhisperModel(str(target), device="cpu", compute_type="int8")  # loads = files are usable
    print(f"ok: {args.model} ({folder_size(target) / 1e6:.0f} MB) loads with int8 on the CPU")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
