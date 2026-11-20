"""Generate a short synthetic lesson recording for testing (T-201).

python scripts/make_test_media.py inbox/test.mp4
python scripts/make_test_media.py out.mp4 --parts "moving:60:noise,static:20:silence"
"""

import argparse
from pathlib import Path

from reel.media.testmedia import DEFAULT_LESSON, make_lesson, parse_parts

DEFAULT_FFMPEG = Path(__file__).resolve().parents[1] / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path)
    parser.add_argument(
        "--parts", help="picture:seconds:sound,... (moving|static, tone|noise|silence)"
    )
    parser.add_argument("--ffmpeg", type=Path, default=DEFAULT_FFMPEG)
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--faststart", action="store_true")
    args = parser.parse_args(argv)
    parts = parse_parts(args.parts) if args.parts else DEFAULT_LESSON
    path = make_lesson(
        args.ffmpeg, args.target, parts, audio=not args.no_audio, faststart=args.faststart
    )
    print(f"created {path} ({sum(part.seconds for part in parts):g} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
