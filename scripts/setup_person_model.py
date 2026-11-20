r"""Download the person detector used by the board check (T-403, D-040) into models/person.

Usage:
  .venv\Scripts\python scripts\setup_person_model.py               # yolox_tiny (default)
  .venv\Scripts\python scripts\setup_person_model.py yolox_nano    # the smaller model

YOLOX (Megvii, Apache-2.0) ONNX files from the official GitHub release, checked against a
pinned SHA-256. Nothing is installed elsewhere.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from reel.cleaning.presence import MODELS, model_path  # noqa: E402
from setup_ffmpeg import download, verify_checksum  # noqa: E402

NOTICE = """Person detector for Reel's board check (T-403, D-040).
YOLOX by Megvii, Apache License 2.0: https://github.com/Megvii-BaseDetection/YOLOX
Files: {files}
"""


def install(name: str) -> Path:
    target = model_path(ROOT / "models", name)
    url, sha256 = MODELS[name]
    if target.is_file():
        verify_checksum(target, sha256)
        print(f"already installed: {target}")
        return target
    print(f"downloading {url} ...")
    download(url, target)
    try:
        verify_checksum(target, sha256)
    except Exception:
        target.unlink()
        raise
    files = ", ".join(f"{n}.onnx ({MODELS[n].url})" for n in MODELS)
    (target.parent / "README.txt").write_text(NOTICE.format(files=files), encoding="utf-8")
    print(f"installed {target}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", nargs="?", default="yolox_tiny", choices=sorted(MODELS))
    install(parser.parse_args().model)


if __name__ == "__main__":
    main()
