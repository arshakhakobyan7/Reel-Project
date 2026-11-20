"""Install Ollama (MIT) into tools/ollama and pull models into models/ollama (T-304).

Usage:
  .venv\\Scripts\\python scripts\\setup_ollama.py                  # install the pinned build
  .venv\\Scripts\\python scripts\\setup_ollama.py --pull gemma3:4b # also download a model

The portable Windows build is downloaded from the official GitHub release and checked
against its published SHA-256. Nothing is installed system-wide: the program stays in
tools/ollama and the models in models/ollama (OLLAMA_MODELS), so deleting ReelProject
removes everything.
"""

import argparse
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from setup_ffmpeg import download, verify_checksum  # noqa: E402

VERSION = "v0.40.16"
ARCHIVE = "ollama-windows-amd64.zip"
URL = f"https://github.com/ollama/ollama/releases/download/{VERSION}/{ARCHIVE}"
SHA256 = "50fcf5ad54277f2fa5d173ebfc93bcb017adbc0c9ef2314daabc96ca9e6aa64e"
DEST = ROOT / "tools" / "ollama"
MANIFEST = DEST / "INSTALL.json"


def install() -> Path:
    exe = DEST / "ollama.exe"
    installed = MANIFEST.is_file() and json.loads(MANIFEST.read_text("utf-8")).get("version")
    if exe.is_file() and installed == VERSION:
        print(f"already installed: Ollama {VERSION} in {DEST}")
        return exe
    archive = ROOT / "tools" / ".downloads" / ARCHIVE
    if not archive.is_file():
        print(f"downloading {URL} ...")
        download(URL, archive)
    verify_checksum(archive, SHA256)
    print("checksum ok; extracting ...")
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(DEST)
    if not exe.is_file():
        raise SystemExit(f"ERROR: {exe} is missing after extraction")
    MANIFEST.write_text(json.dumps({"version": VERSION, "sha256": SHA256}), encoding="utf-8")
    archive.unlink()
    print(f"installed Ollama {VERSION} in {DEST}")
    return exe


def pull(model: str, base_url: str) -> None:
    """Download a model through the running server's API, printing progress."""
    request = urllib.request.Request(
        f"{base_url}/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    last = ""
    with urllib.request.urlopen(request, timeout=600) as response:
        for line in response:
            event = json.loads(line)
            if "error" in event:
                raise SystemExit(f"ERROR: {event['error']}")
            status = event.get("status", "")
            if event.get("total") and event.get("completed"):
                status += f" {event['completed'] / event['total']:.0%}"
            if status.split(" ")[0] != last.split(" ")[0] or status.endswith("0%"):
                print(status, flush=True)
            last = status
    print(f"ok: {model}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pull", nargs="*", default=[], metavar="MODEL", help="models to pull")
    args = parser.parse_args(argv)
    install()
    if args.pull:
        from reel.config import load_config
        from reel.llm.ollama import OllamaServer

        cfg = load_config(ROOT / "config" / "reel.toml")
        with OllamaServer(cfg) as server:
            for model in args.pull:
                pull(model, server.base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
