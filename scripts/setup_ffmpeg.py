"""Install a pinned, checksum-verified FFmpeg build into ``tools/ffmpeg`` (Windows).

Reel keeps FFmpeg inside the project folder instead of installing it system-wide (D-015).
The build is the free "essentials" build by Gyan Doshi (FFmpeg is GPL-3.0 licensed),
downloaded from its official GitHub mirror. The pinned SHA-256 was cross-checked on
2026-11-18 against two independent sources: the GitHub release asset digest and the
winget-pkgs manifest ``Gyan.FFmpeg.Essentials`` 9.0.1.

Usage, from the project root:
    .venv\\Scripts\\python scripts\\setup_ffmpeg.py            install (no-op if already installed)
    .venv\\Scripts\\python scripts\\setup_ffmpeg.py --check    only verify the installed build
    .venv\\Scripts\\python scripts\\setup_ffmpeg.py --force    download and install again
"""

import argparse
import hashlib
import json
import shutil
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from reel.media.ffmpeg_check import (
    REQUIRED_ENCODERS,
    REQUIRED_FILTERS,
    CapabilityReport,
    FfmpegError,
    check_capabilities,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = PROJECT_ROOT / "tools" / "ffmpeg"
DOWNLOAD_DIR = PROJECT_ROOT / "tools" / ".downloads"
MANIFEST_NAME = "INSTALL.json"
BINARIES = ("ffmpeg.exe", "ffprobe.exe")
EXTRA_FILES = ("LICENSE", "README.txt")
USER_AGENT = "reel-setup-ffmpeg"
CHUNK = 1024 * 1024
# Folder moves are retried for up to RETRY_ATTEMPTS * RETRY_DELAY seconds (see _retry_busy).
RETRY_ATTEMPTS = 60
RETRY_DELAY = 0.5


@dataclass(frozen=True)
class BuildSpec:
    """One downloadable FFmpeg build and its expected checksum."""

    version: str
    url: str
    sha256: str
    size: int

    @property
    def archive_name(self) -> str:
        return PurePosixPath(urllib.parse.urlparse(self.url).path).name


PINNED_BUILD = BuildSpec(
    version="9.0.1",
    url=(
        "https://github.com/GyanD/codexffmpeg/releases/download/9.0.1/"
        "ffmpeg-9.0.1-essentials_build.zip"
    ),
    sha256="fec81ae03971d9dd4be3ebe02e263bd2ec1d789483f931bdba5f5715e65da2e9",
    size=111_253_802,
)

Downloader = Callable[[str, Path, int], None]
Verifier = Callable[[Path, Path], object]


class SetupError(RuntimeError):
    """The FFmpeg build could not be installed or is not usable."""


class ChecksumError(SetupError):
    """The downloaded archive does not have the expected SHA-256."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path, expected_size: int = 0) -> None:
    """Download ``url`` to ``target`` through a ``.part`` file, printing progress."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as out:
        total = int(response.headers.get("Content-Length") or expected_size or 0)
        done = 0
        next_percent = 10
        while chunk := response.read(CHUNK):
            out.write(chunk)
            done += len(chunk)
            if total and done * 100 >= next_percent * total:
                print(f"  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", flush=True)
                next_percent += 10
    part.replace(target)


def verify_checksum(path: Path, expected: str) -> None:
    actual = sha256_of(path)
    if actual.lower() != expected.lower():
        raise ChecksumError(
            f"SHA-256 mismatch for {path.name}: expected {expected.lower()}, got {actual}"
        )


def extract_build(archive: Path, dest: Path) -> dict[str, str]:
    """Copy ffmpeg, ffprobe and the licence files out of the archive into ``dest``.

    Only these fixed names are written, so archive paths can never escape ``dest``.
    Returns ``{relative path: sha256}`` for the written files.
    """
    with zipfile.ZipFile(archive) as zf:
        wanted: dict[str, zipfile.ZipInfo] = {}
        for info in zf.infolist():
            parts = PurePosixPath(info.filename).parts
            if len(parts) == 3 and parts[1] == "bin" and parts[2] in BINARIES:
                wanted[f"bin/{parts[2]}"] = info
            elif len(parts) == 2 and parts[1] in EXTRA_FILES:
                wanted[parts[1]] = info
        missing = [f"bin/{name}" for name in BINARIES if f"bin/{name}" not in wanted]
        if missing:
            raise SetupError(f"{archive.name} does not contain {', '.join(missing)}")
        hashes: dict[str, str] = {}
        for rel, info in sorted(wanted.items()):
            out_path = dest / rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, out_path.open("wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK)
            hashes[rel] = sha256_of(out_path)
    return hashes


def read_manifest(dest: Path) -> dict | None:
    try:
        return json.loads((dest / MANIFEST_NAME).read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError:
        return None


def is_installed(dest: Path, spec: BuildSpec) -> bool:
    """True if ``dest`` holds exactly this build and its binaries are unchanged."""
    manifest = read_manifest(dest)
    if not manifest or manifest.get("version") != spec.version:
        return False
    if manifest.get("sha256") != spec.sha256:
        return False
    files = manifest.get("files", {})
    for name in BINARIES:
        rel = f"bin/{name}"
        path = dest / rel
        if rel not in files or not path.is_file() or sha256_of(path) != files[rel]:
            return False
    return True


def install(
    spec: BuildSpec = PINNED_BUILD,
    dest: Path = DEFAULT_DEST,
    download_dir: Path = DOWNLOAD_DIR,
    *,
    force: bool = False,
    keep_archive: bool = False,
    downloader: Downloader = download,
    verifier: Verifier | None = None,
) -> str:
    """Install ``spec`` into ``dest``. Returns ``"installed"`` or ``"already-installed"``.

    The new build is prepared in a staging folder and only replaces ``dest`` after the
    checksum and the optional ``verifier`` succeed, so a failure keeps the old install.
    """
    if not force and is_installed(dest, spec):
        return "already-installed"

    archive = download_dir / spec.archive_name
    if archive.is_file() and sha256_of(archive) == spec.sha256.lower():
        print(f"Using the already downloaded {archive.name}", flush=True)
    else:
        print(f"Downloading FFmpeg {spec.version} from {spec.url}", flush=True)
        downloader(spec.url, archive, spec.size)
    try:
        verify_checksum(archive, spec.sha256)
    except ChecksumError:
        archive.unlink(missing_ok=True)
        raise
    print(f"Checksum OK ({spec.sha256})", flush=True)

    staging = dest.with_name(dest.name + ".staging")
    if staging.exists():
        _retry_busy(lambda: shutil.rmtree(staging), f"remove {staging}")
    staging.mkdir(parents=True)
    try:
        files = extract_build(archive, staging)
        manifest = {
            "version": spec.version,
            "url": spec.url,
            "sha256": spec.sha256,
            "installed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "files": files,
        }
        (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if verifier is not None:
            verifier(staging / "bin" / "ffmpeg.exe", staging / "bin" / "ffprobe.exe")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if dest.exists():
        old = dest.with_name(dest.name + ".old")
        if old.exists():
            _retry_busy(lambda: shutil.rmtree(old), f"remove {old}")
        _retry_busy(lambda: dest.rename(old), f"move the previous build to {old}")
        try:
            _retry_busy(lambda: staging.rename(dest), f"move the new build to {dest}")
        except SetupError:
            old.rename(dest)  # put the previous build back
            raise
        shutil.rmtree(old, ignore_errors=True)
    else:
        _retry_busy(lambda: staging.rename(dest), f"move the new build to {dest}")

    if not keep_archive:
        archive.unlink(missing_ok=True)
        if download_dir.is_dir() and not any(download_dir.iterdir()):
            download_dir.rmdir()
    return "installed"


def _retry_busy(action: Callable[[], object], what: str) -> None:
    """Run a folder move/delete, retrying while Windows reports the files as in use.

    Antivirus scanners and the search indexer briefly open newly written executables,
    so moving or deleting their folder can fail with "Access is denied" for a while.
    """
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            action()
            return
        except PermissionError as exc:
            if attempt == RETRY_ATTEMPTS:
                raise SetupError(
                    f"could not {what}: another program (often antivirus) is still using "
                    f"the files. Wait a minute and run the script again. ({exc})"
                ) from exc
            time.sleep(RETRY_DELAY)


def verify_build(ffmpeg: Path, ffprobe: Path) -> CapabilityReport:
    """Check the build has every required filter and encoder; raise SetupError if not."""
    report = check_capabilities(ffmpeg, ffprobe)
    if not report.ok:
        missing = ", ".join(report.missing_filters + report.missing_encoders)
        raise SetupError(f"the FFmpeg build is missing required features: {missing}")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the pinned FFmpeg build into tools/ffmpeg (Windows)."
    )
    parser.add_argument("--check", action="store_true", help="only verify the installed build")
    parser.add_argument("--force", action="store_true", help="download and install again")
    parser.add_argument(
        "--keep-archive", action="store_true", help="keep the downloaded zip in tools/.downloads"
    )
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print(
            "This script installs the Windows build. On Linux or macOS install FFmpeg with "
            "the system package manager and point the configuration to it."
        )
        return 2

    ffmpeg = DEFAULT_DEST / "bin" / "ffmpeg.exe"
    ffprobe = DEFAULT_DEST / "bin" / "ffprobe.exe"
    try:
        if args.check:
            if not is_installed(DEFAULT_DEST, PINNED_BUILD):
                pinned = PINNED_BUILD.version
                print(f"WARNING: {DEFAULT_DEST} does not hold the pinned build {pinned}")
        else:
            status = install(
                force=args.force, keep_archive=args.keep_archive, verifier=verify_build
            )
            if status == "already-installed":
                print(f"FFmpeg {PINNED_BUILD.version} is already installed - nothing to do.")
            else:
                print(f"FFmpeg {PINNED_BUILD.version} installed in {DEFAULT_DEST}")
        report = verify_build(ffmpeg, ffprobe)
    except (SetupError, FfmpegError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"ffmpeg : {report.ffmpeg_version}")
    print(f"ffprobe: {report.ffprobe_version}")
    print(
        f"All {len(REQUIRED_FILTERS)} required filters and "
        f"{len(REQUIRED_ENCODERS)} required encoders are present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
