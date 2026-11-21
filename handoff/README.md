# Handoff rules for the second developer (GPT / Codex)

Claude is the lead developer of Reel and works in this same folder **at the same time** as you.
You get one task at a time from a file `handoff/T-xxx.md`. Claude reviews your work afterwards.

## Before you start
1. Read `PROGRESS.md` (status), `ARCHITECTURE.md`, your task in `TASKS.md`, and the decisions
   (`DECISIONS.md`, `D-nnn`) named in your task file.
2. Read the existing code your task builds on (named in the task file) and follow its style.

## Hard rules
1. **Only touch the files listed under "Your files"** in the task file (create them; they are new).
   Do NOT edit anything else — especially `TASKS.md`, `PROGRESS.md`, `DECISIONS.md`,
   `ARCHITECTURE.md`, `README.md`, `pyproject.toml`, `src/reel/config/`, `src/reel/db/`,
   `src/reel/cli.py`, `config/*.toml` and existing tests. If you need a change there (a config
   key, a DB column, a dependency, a CLI command), write it exactly under "Requested changes" in
   your report; Claude applies it.
2. **Never open or read** `samples/` (private lesson recordings), `.env`, `data/`, `logs/`,
   `config/reel.toml`, `config/groups.toml`, `config/consent.json`. The `*.example.*` files are fine.
   **Never touch `C:\Users\User\Desktop\ReelProject-live`**: the live server runs from it (D-069).
3. **Free software only (D-009).** No new dependencies; use the standard library
   (`urllib.request`, `http.server`, `json`, `threading`, ...). If one is truly needed, stop and ask
   in the report.
4. **No placeholders**: no `TODO`, `pass` stubs, `NotImplementedError` for required behaviour, or
   fake success. If something cannot be done, say so in the report.
5. **No network in tests.** Mock servers bind to `127.0.0.1` on a free port. No real tokens.
6. **Secrets** come from environment variables (names in `.env.example`) and are never logged,
   printed or put in exception messages.
7. **Style:** Python 3.14, type hints, short English docstrings, line length 100, timezone-aware
   UTC datetimes (`reel.db.utcnow`), module loggers `logging.getLogger("reel.<area>")`.
   Look at `src/reel/media/ffmpeg.py`, `src/reel/queue/jobs.py`, `tests/unit/test_watcher.py`.
8. **No git commands.**

## Checks (all must pass — the whole suite, not only your tests)
```powershell
.venv\Scripts\python -W error -m pytest -q
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
```

## When you finish
Write `handoff/T-xxx.report.md`:
- files created;
- design in a few lines (public API: classes and functions with signatures);
- each acceptance criterion → the test(s) that cover it;
- the real output summary of the two check commands (counts) — never claim a result you did not run;
- "Requested changes" (exact config keys/defaults, columns, etc.), open questions, known limits.
