"""Allow running Reel as ``python -m reel``."""

from reel.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
