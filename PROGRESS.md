# Reel — Development Progress

> **Primary source of truth for the project's state.** Read this file first when resuming work, then TASKS.md and DECISIONS.md.

## Current status

| | |
|---|---|
| **Overall status** | In development. Every pipeline stage exists and a job goes from the folder to Done (YouTube live since 2026-11-20 20:30; Slack, Classroom and notifications in dry-run). 59 of 61 tasks done; T-1002 (user review) and T-1003 (accounts) remain. |
| **Current phase** | Phase 10 (verification, user requests) |
| **Last completed task** | T-1017 — «Հատվածավորում» section heading (GPT, reviewed; 2026-11-21 19:10) |
| **Current task** | T-1018 — on hold: the Google Cloud Free Trial needs a $30 prepayment for the user's card, and the user cancelled it. Code and deploy kit ready (any Ubuntu server); waiting for the user's choice of host |
| **Next task** | The user's choice: review of T-1002 / the 15-minute clip, follow-ups (a) faster silence check, (b) group vocabulary, (c) start phrases |
| **Blocked tasks** | T-1003 needs free test accounts/credentials (Google/YouTube, Slack, Classroom). |
| **Waiting for approval** | Since 2026-11-20 14:10: report after each task and wait for the user's OK before the next. |
| **Given to GPT (D-031)** | T-401, T-502, T-503 (+fix), T-601, T-702, T-703, T-704, T-705, T-801, T-1007 done and reviewed. Nothing new until the user says so. Later candidates: T-806, T-902, T-903, T-1004 |

### Outstanding questions (to ask when the related task comes up)
| Question | Needed before | Default used until answered |
|---|---|---|
| Break rule — answered: nobody at the board > 10 s → 5 s, also with speech (D-040) | T-403 | — |
| Ollama location — answered: inside ReelProject (`tools/ollama`, `models/ollama`); unneeded models deleted | T-304 | — |
| Real Armenian lesson recording(s): the user offered a video on 2026-11-18; to be placed in `samples/` (private, not in git) | T-303, T-1002 | Synthetic media + fakes |
| Dashboard UI language — answered: Armenian (D-049) | T-802 | — |
| Q-3 What time counts as "same evening"? | T-805 (done with the default, D-050) | Cutoff 23:59 local time (config) |
| Q-4 How recordings reach the folder; formats; local or synced folder | T-202 | Local/synced folder; mp4, mkv, mov, webm, m4v, avi |
| Q-5 Confirm folder/file naming convention (D-018) | T-203 | Answered: staff name the files; a missing number waits for staff (D-018 details) |
| Q-8 YouTube channel owner; do group playlists exist; may Reel create them? | T-702 | Playlist IDs from `groups.toml`; creation off |
| Q-9 Slack channel per group, message format; Classroom announcement or material | T-703, T-704 | D-022 |
| Q-11 Retention periods | T-902 (done with the defaults, D-054) | 30 days working files, 365 days transcripts, sources kept (D-021) |
| Q-12 Expected volume (recordings per day, length, peak) | T-303, T-805 | 1 heavy job at a time |
| Q-13 Title template (e.g. "Lecture" vs "Դաս") and thumbnail style | T-502, T-503 | `{group} · Lecture {lesson}`, frame + title overlay |
| Credentials: Google Cloud project (YouTube + Classroom OAuth), Slack bot token | T-1003 | Dry-run mode (D-016) |
| Licence of the Reel code itself (academy's choice) | T-1004 | None set in pyproject.toml |

### Important technical decisions (details in DECISIONS.md)
- Python 3.14 venv, FastAPI, SQLite persistent job queue (D-001, D-011).
- Armenian lessons; local faster-whisper; local Ollama LLM; everything free (D-002, D-003, D-004, D-009).
- Lesson start/end from the lecturer's phrases → transcript first → stage order `Imported → Transcribed → Cleaned → Chapters → Consent → Uploaded → Posting links` (D-005, D-012).
- SQLite database `data/reel.db` through SQLAlchemy 2.1: WAL, `BEGIN IMMEDIATE` for read-then-write work, UTC times, ids never reused, schema versions with `reel db init/upgrade` (D-027).
- Job queue: one locked transaction per operation, oldest due job first, leases with heartbeats, a crash costs one attempt, two release modes, re-run only from a reached stage (D-028).
- Worker runtime: `reel worker`, one thread per stage attempt, cooperative stop with a grace time, timeouts, notifier hook for failures and holds (D-029).
- Core Engine and Academy Library are other teams' products: ports + contracts + interim implementations (D-008).
- One task at a time with user approval (D-010).

## Final verification (T-1005)

The status of every PRD item. The check covers the code, the automated tests (`-W error`,
ruff clean) and what was run on this PC.

**Legend:**
- ✅ verified by automated tests (and, where stated, on this PC);
- 🟡 verified only against local fakes or mocks: it needs the academy's real accounts or
  systems (T-1003);
- 🟠 needs the user's review of real results (T-1002);
- ⚠️ partly met (see the note).

### Functional requirements

| PRD item | Where | Tests | Status |
|---|---|---|---|
| FR-1 Watch folders | `ingest/watcher.py` | test_watcher, test_e2e (UJ-1) | ✅ |
| FR-2 Wait for a complete file, validate | `ingest/watcher.py`, `media/ffmpeg.py` | test_watcher, test_media, test_import_stage | ✅ |
| FR-3 Identify group and lesson | `ingest/identify.py`, `JobQueue.identify`, upload form | test_identify, test_web_detail, test_import_stage | ✅ |
| FR-4 No duplicates | `stages/importing.py` (hash of all parts) | test_import_stage | ✅ |
| FR-5 Background jobs, stage tracking | `queue/` | test_queue, test_worker | ✅ |
| FR-6 Silence detection | `cleaning/silence.py` | test_silence, test_silence_logic | ✅ (real lesson: T-406) |
| FR-7 Chatter before/after class | `cleaning/boundaries.py` | test_boundaries, test_clean_stage | ✅ / 🟠 tuning on more lessons |
| FR-8 Break | `cleaning/presence.py` (empty board, D-040) | test_presence, test_presence_logic | ✅ |
| FR-9 Cut list stored, shown | `cleaning/cutlist.py`, detail page | test_cutlist, test_web_detail | ✅ |
| FR-10 Render with padding | `media/render.py` | test_render | ✅ (real 4K lesson: T-405) |
| FR-11 Loudness normalisation | `media/render.py` (two-pass loudnorm) | test_render | ✅ (−28.9 → −16.2 LUFS on the real lesson) |
| FR-12 Original and final duration | import, cut list, dashboard | test_web_dashboard, test_web_api | ✅ |
| FR-13 Transcript times match the cut video | `cleaning/remap.py`, part audio join | test_remap, test_import_stage | ✅ |
| FR-14 Store the transcript | `stt/transcript.py` | test_transcript, test_transcribe_stage | ✅ |
| FR-15 Chapters (YouTube rules) | `metadata/chapters.py`, `metadata/text.py` | test_chapters, test_chapters_stage, test_metadata_text | ✅ / 🟠 |
| FR-16 Title template | `metadata/text.py` | test_metadata_text | ✅ |
| FR-17 Description with chapters | `metadata/text.py` | test_metadata_text | ✅ |
| FR-18 Thumbnail | `media/thumbnail.py` | test_thumbnail, test_thumbnail_logic | ✅ |
| FR-19 Transcript-only, validated LLM, fallback | `llm/`, quote checks (D-039, D-044) | test_llm, test_chapters, test_boundaries | ✅ |
| FR-20 Consent status from the Core Engine | `consent/` (file now, HTTP contract) | test_consent | 🟡 the Core Engine does not exist yet |
| FR-21 Hold on opt-out or unreachable | `stages/consent.py` | test_consent_stage, test_e2e (UJ-3) | ✅ |
| FR-22 Consent log | `consent_checks` rows, release audit | test_consent_stage, test_web_detail | ✅ |
| FR-23 YouTube upload, unlisted | `publish/youtube.py`, `stages/publishing.py` | test_youtube, test_publishing_stages, test_e2e (UJ-5) | 🟡 |
| FR-24 Group playlist | `publish/youtube.py` | test_youtube | 🟡 |
| FR-25 Title, description, thumbnail on the video | `publish/youtube.py` | test_youtube | 🟡 |
| FR-26 Slack post | `publish/slack.py` | test_slack, test_publishing_stages | 🟡 |
| FR-27 Classroom post | `publish/classroom.py` | test_classroom, test_publishing_stages | 🟡 |
| FR-28 Academy Library | `library/` (local catalogue now), API `/api/v1/library` | test_library, test_web_api | ✅ local / 🟡 HTTP |
| FR-29 Independent posting targets | `stages/publishing.py` | test_publishing_stages | ✅ |
| FR-30 Status by stage (dashboard) | `web/` | test_web_dashboard, test_web_detail | ✅ (HTML tests; see limitation 6) |
| FR-31 Automatic retries with backoff | `queue/jobs.py` | test_queue, test_e2e (UJ-4) | ✅ |
| FR-32 Re-run from a stage | dashboard, `reel rerun` | test_web_detail, test_runtime, test_e2e | ✅ |
| FR-33 Staff notifications | `notify/staff.py` | test_notify | ✅ dry-run / 🟡 real Slack |
| FR-34 Configuration outside code | `config/` | test_config_* | ✅ |

### Non-functional requirements

| PRD item | How | Status |
|---|---|---|
| NFR-1 Timeliness | T-1002: 2 h 10 min of work for a 1 h 40 min lesson on this PC (CPU only), plus the real upload | ✅ for a lesson that arrives by about 21:30 / 🟠 the user's review |
| NFR-2 Reliability, idempotency | Leases, resumable YouTube session, marker recovery for posts, restart test with a killed process | ✅ (test_restart, test_e2e, test_publishing_stages) |
| NFR-3 Concurrency | Worker slots, limit on heavy stages | ✅ (test_worker) |
| NFR-4 Output quality | 4K→1080p, QSV quality 26, −16 LUFS; the user judged the real render | ✅ / 🟠 |
| NFR-5 Privacy and security | Local STT/LLM, secrets in Credential Manager or env, staff login, CSRF, CSP, unlisted only, API without private data | ✅ (test_google_auth, test_web_auth, test_web_api) |
| NFR-6 Data retention | `reel.retention` | ✅ (test_retention) |
| NFR-7 Observability | JSON logs, per-job logs, events, metrics page | ✅ |
| NFR-8 Cost | Compute time and tokens, time budget with an alert | ✅ (test_metrics) |
| NFR-9 Configurability | TOML + groups + prompts + `REEL__` overrides, re-read per stage | ✅ |
| NFR-10 Maintainability | Stage contract, documentation, 1,170+ tests | ✅ |

### User journeys and constraints

| Item | Status |
|---|---|
| UJ-1 Happy path | ✅ test_e2e: file → Done with dry-run publications |
| UJ-2 An absent student catches up | ✅ chapters, description and links are produced / 🟡 real posting |
| UJ-3 Consent hold | ✅ test_e2e |
| UJ-4 Failure and recovery | ✅ test_e2e, test_restart |
| UJ-5 Wrong cut reported | ✅ test_e2e (re-run, no duplicate posts) |
| C-1 Unlisted only | ✅ a constant in code (D-017); the R-5 private lock is flagged and alerted |
| C-2 Group playlist | ✅ / 🟡 |
| C-3 Opted-out students off camera | ⚠️ see limitation 4 |
| C-4 Starts from a watched folder | ✅ (the dashboard upload is a second way in) |
| C-5 Named technologies | ✅ FFmpeg, a job queue, Whisper, a local LLM, the YouTube Data API |
| M-1 … M-6 Success metrics | ✅ the metrics page and JSON (test_metrics) |
| M-7 Viewing analytics | not built (PRD phase 3) |

### Known limitations

1. **Live publishing is not proven.** YouTube, Slack and Classroom were tested only against
   local fakes that follow the official APIs. The first real run needs the academy's free
   accounts and the Google project (T-1003, `docs/setup/google.md`).
   - An External Google app in *Testing* loses access after 7 days.
   - Videos uploaded by an API project that Google has not audited can be locked to private
     (R-5). Reel flags and alerts this; the fix is Google's free audit form.
2. **Core Engine and Academy Library.** Their products do not exist yet. Reel uses a consent
   file and a local catalogue, and their HTTP contracts are tested only against mocks
   (`docs/integrations/`).
3. **Time.** On this PC (CPU only), a 1 h 40 min lesson took 2 h 10 min of work (T-1002):
   transcription 69 min, cleaning 52 min, chapters 8 min. The real upload is not measured yet
   (T-1003). "Same evening" holds for a lesson that arrives by about 21:30 (cutoff 23:59).
4. **C-3: Reel does not hide people in the picture.** When a student has opted out, or a
   status is unknown, the video is held for staff, who decide (D-019). Reel never publishes
   such a lesson on its own, but it has no face blurring.
5. **Lesson boundaries and chapters** are tuned on one real lesson (two parts). Other
   teachers' phrases may need additions to the phrase lists (`config/reel.toml`) after
   T-1002 and the first weeks.
6. **Browser checks.** The dashboard was tested through its HTML and HTTP (FastAPI
   TestClient and a real uvicorn server), but no one has looked at it in a browser yet. The
   upload script (`upload.js`) is not covered by the Python tests; its server side is.
7. **Autostart** is installed by the user once (`scripts\autostart.ps1 install`). The agent
   was not allowed to register a scheduled task.
8. **One Windows PC.** SQLite, the Windows secret store and Quick Sync. There is no Linux or
   Docker deployment (D-055). For access from other computers, the dashboard needs an HTTPS
   reverse proxy.
9. **Models.** Gemma 3 is free under Google's Gemma Terms of Use, which is not an OSI
   licence. It can be swapped for an Apache-2.0 model (THIRD_PARTY.md).
10. **Open questions answered with defaults** (the user may change them):
    - Q-3: the same-evening cutoff is 23:59.
    - Q-8: playlists come from `groups.toml`, and Reel does not create them.
    - Q-9: Slack per group, and a Classroom announcement.
    - Q-11: retention is 30 and 365 days, and sources are kept.
    - T-807's questions: 20 GB per file, any staff member may upload, and form values win.
    - Q-12: one heavy job runs at a time.
    - Q-13: the title template is `{group} · Lecture {lesson}`, and the thumbnail is a frame with the title over it.
    - Reel's own code licence is not chosen yet.
11. **Security.** There is no two-factor login. The login throttling is per process (in
    memory). The dashboard is meant to stay on 127.0.0.1 or behind HTTPS.
12. **The PC must stay plugged in.** Windows sleeps on a low battery or a closed lid, and no
    work runs then (found in T-1002). Reel keeps the PC from sleeping for being idle while a
    stage runs, and does not count the time asleep (D-056).

## Working agreement (from the user)

1. The project root is `C:\Users\User\Desktop\ReelProject`. All project files, dependencies, tests, logs and generated files live inside it.
2. Everything used to build or run Reel must be **free** (D-009).
3. **After each task:** report what was done, which files changed, what was tested and whether the acceptance criteria passed, then say what comes next and **wait for approval** (D-010).
4. Stop and ask when something is unclear or would need an important assumption; record answers in this file and in DECISIONS.md.
5. Update this file after every task: keep the history, record failures and partial work, and never mark a task done without verified acceptance criteria.
6. Conversation with the user is in Armenian; project documents and code are in English (D-023).
7. Git: a local repository since 2026-11-20 (branch `main`, no remote). Commits are made only when the user asks for them; nothing is pushed anywhere without the user's say. `.gitignore` keeps secrets, local config, data, logs, recordings, tools and models out.

## Resume checklist (for a new session)

1. Open `C:\Users\User\Desktop\ReelProject` and read this file (status table + latest log entry).
2. Read the task summary table in TASKS.md and the decision table in DECISIONS.md.
3. Compare the log with the real files: list the folders, and once code exists run the tests (`.venv\Scripts\python -m pytest`).
4. If a task is marked IN PROGRESS, check what part of it exists and continue from there; do not restart finished work.
5. Before starting a new task, make sure the user approved it (D-010).

## Environment snapshot (2026-11-18)

| Item | State |
|---|---|
| OS | Windows 11 Pro 10.0.26200 |
| CPU / RAM / GPU | Intel Core i5-1335U (12 threads) / 15.7 GB / no NVIDIA GPU (Intel Iris Xe only) |
| Free disk (C:) | ~197 GB |
| Python | System default 3.15.1 (Python install manager 26.4). Python 3.14.10 installed on 2026-11-18 for the project venv `.venv` (D-011) |
| Git | 2.55.0 |
| winget | 1.29 (FFmpeg 9.0.2 available as `Gyan.FFmpeg`, not used — see D-015) |
| Docker Desktop | Installed, daemon not running; not used (D-009) |
| FFmpeg | 9.0.1 essentials build, project-local in `tools/ffmpeg` (T-002); not installed system-wide |
| SQLite | 3.50.4, bundled with Python 3.14.10; used through SQLAlchemy 2.1.9. Database `data/reel.db` created 2026-11-18 (T-101) |
| Node.js, Ollama, Redis | Not installed |

---

## Log (newest first)

### 2026-11-22 — The project on GitHub (user request)

- **The user:** made the private repository `arshakhakobyan7/Reel-Project` and asked for the
  whole project there, so that a friend can pull it and run it.
- **Checked before the push:**
  - Only committed files go (260). `.env`, `config/reel.toml`, `config/groups.toml`,
    `config/google-client*.json`, `config/consent.json`, `data/`, `models/`, `tools/` and the
    recordings stay ignored.
  - No secret in any commit; only test placeholders.
  - The repository is private (the public API answers 404) and was empty.
- **A fresh clone was tested** in the scratchpad, following README:
  - venv, `pip install -e ".[dev]"`, `reel config init`, `reel db init`, `reel config check`;
  - the full suite: 1440 passed, 4 skipped (POSIX-only), -W error;
  - only FFmpeg and the models were linked from this PC, in place of the setup scripts.
- **README:**
  - `git clone` with the repository's address, and the collaborator note;
  - `mkdir inbox`, because `reel config check` failed on a fresh clone without it.
- **Pushed:** `main` to `origin` (GitHub).
- **Note:** the commits carry the author's Git email, which the repository's collaborators can
  see.

### 2026-11-22 — T-1018: the Google Cloud route is dropped (no cloud resources were created)

- **Google Cloud:**
  - At sign-up it asked for a one-time **$30 prepayment** for the user's card ("refundable if
    the Cloud billing account is closed"). The console showed "Your Free Trial is pending —
    Prepayment is needed".
  - Adding another card did not help.
  - The user did not pay and asked how to close the billing account and remove the card.
    Claude gave the steps. The billing account "My Billing Account" is now **closed** (the
    console says so; no projects linked). The user removes the card at payments.google.com.
- **What stays:** the Linux readiness and `deploy/gcp/` (commit `41459d9`) work on any Ubuntu
  24.04 server.
- **Waiting:** the user's choice of where to run Reel, or to stop T-1018.

### 2026-11-22 — T-1018 Reel online for a few days (D-070) — IN PROGRESS: code and deploy kit ready

- **The user:**
  - Asked whether "Google Cloud Free Trial + Compute Engine" (GPT's advice) is a free way to
    deploy. It is a 90-day trial, not free hosting, and no Always Free tier can run Reel
    (official pages checked).
  - Then: only a few days are needed, for reviewers. So the Free Trial fits.
  - Choices: YouTube live, real lessons too, open registration.
- **Linux readiness:**
  - The code already guards Windows calls: `CREATE_NO_WINDOW`, `power.py`, process checks,
    `hwaccel`, the encoder falls back to libx264.
  - All key packages have Linux 3.14 wheels (PyPI checked).
  - Two Windows-only parts got a Linux version:
    - `PosixSecretStore` (`auth.store = "posix"`, owner-only file, refuses loose
      permissions);
    - `KeySessionCodec` (AES-GCM, key in the store) through `default_session_codec(tokens)`
      in `YoutubePublisher`.
  - Tests: `tests/unit/test_server_secrets.py` (4 of them run only on POSIX) and a
    crash-resume test with the key codec in `test_youtube.py`.
- **Deploy kit `deploy/gcp/`:**
  - `setup.sh`: apt FFmpeg + Caddy, uv + Python 3.14, Reel, Ollama v0.40.16 Linux checked by
    SHA-256, the models, `.env`, systemd, Caddy;
  - `reel.service`, `Caddyfile.template`, `server.env.template`, `README.md`;
  - `.gitattributes` keeps LF for them.
- **Checks:** full suite 1440 passed, 4 skipped (POSIX-only), -W error; ruff clean.
- **Next:**
  - the user starts the Free Trial;
  - Claude installs the Google Cloud CLI on the PC, the user signs in once;
  - then the VM, the copy, `setup.sh`, the token move, the tests on Linux and one lesson
    end to end.

### 2026-11-21 20:10 — New backup of the clean database (user request)

- New: `data/backups/reel-clean-20261121T160828Z.db`, made with SQLite's online backup
  (`backup_database`). Its content was checked: integrity ok, schema 3, the admin account (#1)
  and job #2. It was made a single file (journal mode DELETE).
- Deleted after that: `reel-v1-20261121T130642Z.db`. It is now the only backup.

### 2026-11-21 20:00 — Cleanup, part 2 (the user: "delete all of them, the 1st too")

- **Deleted:**
  - `data/trial` (the T-1002 full-lesson render, 1.2 GB);
  - the backups `reel-v2` and `reel-before-cleanup` (with their side files), which held the
    deleted account;
  - the admin's cancelled jobs #1 and #3, with their rows (cascade).
- **Log:** 67 lines of the deleted account and its jobs #4–#7 were removed from
  `logs/reel-serve.jsonl`; no line names it any more. The live server was stopped while the log
  and the database were changed.
- **Codex's external test:**
  - stopped with its own `stop.ps1`: the tunnel first, then its server; port 8091 is closed and
    no process is left;
  - deleted: `tools/public-test` and `tools/cloudflared`, and
    `Desktop\warehouse\reel-test-access.json`, which held its test password. The warehouse
    repo is clean.
  - Checked first: it published nothing; its one job, a 19 s file, failed at import.
  - Nothing in `src`, `tests` or `scripts` refers to it.
- **Database now:**
  - the admin account (#1) and job #2 only. Job #2 is the YouTube video; its files are kept.
  - Integrity ok, no foreign-key problems. The only backup left is `reel-v1` (17:06, before
    registration existed).
- **Project:** 8,716 files and 9.6 GB without the regenerated caches. `reel serve` restarted
  19:53; full suite 1430 passed (-W error), ruff clean. Temporary copies in the scratchpad
  were deleted.

### 2026-11-21 19:45 — Project cleanup (the user: "only the clean project should remain")

- **The user:** delete the files not needed for further work, and the registered users with
  the videos they uploaded and that were processed. Be careful not to delete important files.
- **Checks first:**
  - All 10 videos to be deleted were hashed against `Desktop
eel_videos`. All are identical
    copies (IMG_0359, IMG_0360, IMG_0359_first15min, test.MOV), so no original was lost.
  - The trial renders `test_reel.mp4` and `..._reel_v2.mp4` are identical to the files in
    `reel_videos`.
  - No job ran and no upload was open. The live server was stopped during the cleanup.
- **Database** (backup `data/backups/reel-before-cleanup-20261121T153904Z.db`):
  - account #2 (user) and its recordings and jobs #4–#7 were deleted, with their rows
    (cascade): 10 stage runs, 24 events, 1 cut list, 1 metadata row, 2 transcripts;
  - kept: account #1 (admin) and jobs #1, #2, #3;
  - integrity ok, no foreign-key problems.
- **Files** (from an explicit list, with protected paths refused): 390 files, 36.69 GB.
  - Jobs #1, #3–#7: working folders and uploads.
  - `samples/`, `data/trial15`, `data/trial_test`, `data/trial*` logs, and `data/trial`
    except `jobs/1`.
  - The 19 November logs and the caches.
  - The project is now 8,746 files, 11 GB.
- **Kept, for the user to decide:**
  - `data/trial/jobs/1`: the full-lesson T-1002 render given for review, 1.3 GB.
  - The backups `reel-v2` and `reel-before-cleanup`, which still hold the deleted account.
  - Its sign-in lines in `logs/reel-serve.jsonl`.
  - `tools/public-test` (Codex's external test, still running on 8091).
  - The rows of the admin's cancelled jobs #1 and #3.
- **After:** `reel serve` restarted 19:40, pages answer; `reel cleanup --dry-run` has nothing to
  remove. Full suite 1430 passed (-W error), ruff clean.

### 2026-11-21 19:10 — T-1017 «Հատվածավորում» (GPT) reviewed — DONE; deployed

- **GPT's report** (`handoff/T-1017.report.md`):
  - only the two strings in `recording.html` changed: `<h2>Հատվածավորում</h2>` and
    «Հատվածներ չկան։»;
  - 3 tests in `tests/unit/test_chapters_heading.py`. They check the heading and the list, the
    empty text, and that the stage pill still says «Գլուխներ».
- **Review:**
  - the diff is exactly those two lines;
  - the T-1016 cost condition is kept;
  - no existing test changed;
  - own run: ruff clean, 1430 passed (-W error).
- The user asked to commit all uncommitted work. The commit holds the template, the test, the
  report and these notes; nothing else was uncommitted.
- Deployed to `ReelProject-live` (D-069). Before the restart, a check that no job runs (this
  time with a working command).

### 2026-11-21 18:50 — T-1016 "My page" (GPT) reviewed — DONE; deployed to `ReelProject-live`

- **GPT's report** (`handoff/T-1016.report.md`): `/profile` with two forms (details, password),
  sign-in by username or email (username first, one lookup), the cost panel for admins only,
  25 test functions (54 cases) in `tests/unit/test_profile.py`. No existing test changed.
- **Review by Claude:**
  - read every change: `profile.py`, `forms.py` (registration keeps its order and messages),
    `texts.py`, `base.html`, `profile.html`, the two `app.py` spots, and the single
    `recording.html` condition;
  - no account id in the URL; CSRF applies (app-wide dependency, test); wrong current
    passwords use the login throttle; a new session for this browser, and the other sessions
    end (test); logs hold only ids.
  - Own run: ruff clean, 1427 passed (-W error).
- **Noted, not changed:**
  - A password change holds the database write lock for scrypt verify and hash (measured
    128 ms + 132 ms; workers wait up to 30 s): acceptable.
  - Login failures typed as the email and as the username count under different `user:` keys.
    The `client:` key still caps an address.
  - GPT's follow-up: existing log lines name the username (an email for registered accounts):
    sign-in and sign-out, and the staff actions on jobs. To be offered to the user.
- **Commit:** GPT had already started T-1017 in `recording.html`. Only T-1016's hunk (the cost
  condition) was staged, so T-1017's unstaged edits and `test_chapters_heading.py` stay in the
  working tree.
- **Deploy (D-069):** `ReelProject-live` moved to the T-1016 commit; then the full suite in that
  worktree, a check that no job ran, and the restart.

### 2026-11-21 18:35 — T-1017 «Գլուխներ» → «Հատվածավորում» handed to GPT (user request)

- **The user:** "Give a prompt that tells GPT to rename the «Գլուխներ» section to
  «Հատվածավորում»".
- The word «Գլուխներ» also names the stage and appears in flags, and the unclear-speech forms
  say «բաժիններ». Asked the user how far to go: **only that section**.
  - The heading becomes «Հատվածավորում».
  - The empty text «Գլուխներ չկան։» becomes «Հատվածներ չկան։».
- `handoff/T-1017.md`:
  - it is done after T-1016, because both change `recording.html`;
  - 3 tests, including that the stage name «Գլուխներ» stays in the pills (they use
    `texts.STAGE_LABELS`, checked).
- The live server shows it only after the review and a deploy to `ReelProject-live` (D-069).

### 2026-11-21 18:25 — Dashboard outage (500 from 18:17); the live server now runs from `ReelProject-live` (D-069)

- **The user:** "my server does not start: is it GPT's work?"
  - The server ran, but every signed-in page answered 500:
    `UndefinedError: 'module object' has no attribute 'PROFILE_LABELS'`.
  - GPT, working on T-1016 in this folder, had changed `base.html`. The running server
    reloads templates from disk but had loaded the older `texts.py` when it started.
- **Fix:**
  - `git worktree add --detach ../ReelProject-live afb0d27`.
  - `reel serve` restarted 18:23 with `PYTHONPATH=<ReelProject-live>\src`, working directory
    `ReelProject` (real data, config, logs).
  - Checked first that no job was running or held and no upload was open.
  - Checked: `reel` is imported from the worktree, and `/login` and `/register` answer 200.
    The anonymous `/` redirects to login. Dashboard, detail, accounts and auth tests on the
    worktree code: 78 passed.
- GPT's unfinished T-1016 changes in `ReelProject` were left untouched. No report from GPT yet.
- **Job 7** (the user's re-upload of `IMG_0359_first15min.MOV`, only processed): Done at
  18:20. Steps: Clean 613 s (zoom 1.12x, 12 s cut), Chapters 3, then Consent, Upload and Post
  skipped.
- `handoff/README.md` now tells GPT never to touch `ReelProject-live`.

### 2026-11-21 18:12 — T-1016 "My page" handed to GPT (user request, D-068)

- **The user:**
  - Accounts, the admin's too, have no page to check and update their details, for example
    the password. Write a prompt for GPT; Claude reviews the work afterwards.
  - Added during the turn: the «Ծախս և ժամանակ» panel must go from the user's recording page.
  - The user's re-upload of the test video is being processed; they report the result.
- **`handoff/T-1016.md`** holds the request verbatim and Reel's rules:
  - `/profile` for the signed-in account only, with two forms (details; password);
  - registration's validation, reused;
  - the username never changes, and sign-in by username or email;
  - a wrong current password is throttled like a login;
  - a new session for this browser, and the other sessions end;
  - no personal data or passwords in logs;
  - the cost panel shown to admins only;
  - 13 acceptance criteria;
  - a preview on port 8093 with a throwaway database. The commands were tried, with plain
    HTTP and YouTube in dry-run, and answered 200; port 8080 was untouched.
- Claude reviews GPT's report and code and runs the full suite before T-1016 counts as done.

### 2026-11-21 17:52 — T-1015 Duplicates per account (user report, D-067) — DONE

The user: a test from a user's page failed, because every uploaded video was cancelled. The
cause was in the real DB. Account #2 uploaded `test.MOV` (job 4) and `IMG_0359_first15min.MOV`
twice (jobs 5, 6). The admin had already processed those files (recordings 3 and 2), so the
import stage cancelled them as duplicates (FR-4). The message was in English and named the
admin's recording; recording 3 was itself cancelled. The user: fix it, but do not re-run
#4–#6 ("ես այն ինքս կփորձարկեմ").

- **Import** (`stages/importing.py`):
  - A published recording is compared with the published hashes (`content_hash`, unique),
    as before.
  - A recording only processed stores `processed_hash`. It is compared only with the same
    account's recordings in progress, or ready with the video still there (`own_original`).
  - When the uploader may not open the original, the message says "a recording of another
    account".
- **Schema version 3** (`db/versioning.py`): `recordings.processed_hash` with an index; it
  takes the hash of any recording only processed.
- **Real database upgraded** 17:50, after checking that no job ran and no upload was open.
  Backup `data/backups/reel-v2-20261121T135052Z.db`. `reel serve` restarted 17:51;
  `/login` and `/register` answer 200.
- **Page:** a cancelled duplicate says why in Armenian. The link to the original appears only
  for one's own recording, or for an admin.
- **Tests:** 3 import tests with real FFmpeg (another account's copy, the same user again:
  in progress / ready / deleted / cancelled / failed, no details of another account), the
  v2→v3 migration test, and 1 page test. Full suite: 1373 passed (-W error), ruff clean.
- **Still open:**
  - The `tools/public-test` database is still version 1; it needs `reel db upgrade` (to 3)
    before that instance restarts.
  - The history of jobs #4–#6 keeps the old English event text.

### 2026-11-21 17:24 — T-1014 A user's processed video is deleted after 30 days (user request, D-066) — DONE

The user, after the T-1013 report: "Այո թող ջնջվի 30 օր հետո", then "Գործարկիր սերվերը".

- **Retention** (`retention.py`): `[retention] processed_only_days = 30` (0 = keep). For a
  recording with `publish = false`, 30 days after the job ended (Done, Failed or Cancelled),
  the daily cleanup removes the job folder ("processed video") and the upload's folder
  `data/recordings/<upload>/` ("uploaded files"). Never files in a watched folder; never a
  published account's video (D-057 stays). The `cleanup` event now lists the kinds removed.
  `keep_until(job, days)` gives the date.
- **Dashboard:**
  - The upload page says the video is kept 30 days.
  - The recording's page says "download it before <date>", or after the deletion when the
    files were deleted. Re-running is no longer offered once the files are gone.
- **Config:** `processed_only_days = 30` in `reel.toml` and `reel.example.toml`.
  `reel cleanup --dry-run` on the real data: nothing to remove (the 3 recordings are the admin's).
- **Tests:** 3 new in `test_retention.py` (30 days, only Reel's own upload folders, 0 = keep)
  and 1 page test in `test_accounts.py`. Full suite: 1368 passed (-W error), ruff clean.
- `reel serve` restarted with the new code (the user: "Գործարկիր սերվերը").

### 2026-11-21 17:08 — T-1013 Accounts: registration, users and the admin (user request, D-065) — DONE

The user: people with videos to process sign in; without an account they register (first and
last name, email, optional phone); the admin sees the users and what they do and can close an
account. Answers: a new account works at once, but its videos are not published (the user
downloads the processed one); a user sees only their own recordings and decides on them except
consent. Added mid-task: the admin can let chosen users publish to YouTube.

- **Schema version 2** (`db/versioning.py`): `staff_users` role, can_publish, first/last name,
  email (unique), phone; `recordings` uploaded_by_id, publish. Existing accounts become admins;
  existing uploads are linked to their accounts. Test from the real v1 schema
  (`tests/unit/schema_v1.sql`).
- **Web:** `/register` (email = username, 5 accounts an hour per address, signed in at once);
  `/admin/users` and `/admin/users/<id>` (recordings, last 50 actions with notes, close/open,
  allow/stop publishing); a user sees and opens only their own recordings and uploads (else
  404); release, rerun, metrics and accounts are admin-only (403); nav shows the admin links
  only to admins. `reel user add --admin`; `reel user list` shows the role.
- **Pipeline:** `publish = false` → Consent, Upload and Post do nothing ("only processed");
  Chapters needs no group in groups.toml, and a user's typed group is not added there; the
  page offers the download and hides publications; unclear speech asks to "finish".
- **Real database upgraded** 17:06 (backup `data/backups/reel-v1-20261121T130642Z.db`):
  `admin` is admin; recordings 1-3 linked to it. `reel serve` restarted with the new code.
- `tools/public-test` (Codex's external test) still runs the old code; its own database is
  version 1, so it needs `reel db upgrade` before it restarts (not touched: the user's call).
- 24 tests in `tests/unit/test_accounts.py`, `tests/integration/test_processed_only.py`, 2 in
  `test_chapters_stage.py`, the migration test; versioning tests made version-relative.
  Full suite: 1364 passed (-W error), ruff clean.

### 2026-11-21 15:43 — T-1012 Unclear speech waits for staff (user request, D-064) — DONE

The user: tell staff that the speech is not clear and that no chapters could be found; staff
decide to publish or cancel; when publishing, they may set the chapters themselves.

- `stages/chapters.py`: no chapters and fewer than `[metadata] min_words_per_minute` (20)
  words per minute → title, description and thumbnail are stored, then hold `speech_unclear`
  with an Armenian message (word count, minutes). `set_staff_chapters` stores staff chapters
  and rebuilds the description and `metadata.json`.
- Dashboard: for this hold the page shows "Տեղադրել առանց բաժինների" (`/publish`) and a
  chapters field "Պահպանել բաժինները և տեղադրել" (`/chapters`, one `M:SS title` per line,
  YouTube's rules explained in Armenian, the typed text kept on a mistake); cancel as before.
  Both continue with Consent, so consent is still checked.
- Checked with test.MOV in the trial DB: held at Chapters with "0 words"; the real dashboard
  app (HTTPS test client) showed the message and both forms; a two-line list was refused with
  the reason; a three-line list was saved, and the job finished (dry-run) with those chapters
  in the YouTube description.
- README staff table updated. 23 new tests (`tests/unit/test_unclear_speech.py`, 3 in
  `tests/integration/test_chapters_stage.py`). Full suite: 1336 passed (-W error), ruff clean.

### 2026-11-21 15:21 — T-1011 Board framing on a light wall (user request, D-063) — DONE

The user asked why test.MOV was not zoomed to the board ("Այո, սկսիր" to the proposed fix).

- **Causes:** the white board (brightness 174) was like the light wall beside it (164–167),
  so the surface ran to 98 % of the width; and the teacher's legs under the desk (44 % of the
  little work) pulled the work box to the bottom edge.
- **Fix (`reel.cleaning.framing`):** bottom-edge traces with no work in the 8 % above them
  are dropped whatever their share; when the brightness surface covers 92 % of the width or
  height, `frame_lines` finds the board's frame: the nearest long edge lines above and below
  the work, how far they run (gaps up to 1.5 %), and the outermost upright edge near their
  ends (the board's side, not the panel rails).
- **Checked on the saved work maps and stills (`data/trial/framing2/`):** test.MOV: board
  0.125–0.887 × 0.32–0.84, zoom 1.24x (was none); IMG_0359: unchanged, 1.12x.
- test.MOV processed again (trial DB, dry-run): `reel_videos/test_reel.mp4` now zoomed 1.24x,
  all four panels in the picture.
- 5 new tests in `tests/unit/test_framing.py`. Full suite: 1315 passed (-W error), ruff clean.

### 2026-11-21 14:54 — The user's test.MOV processed locally (trial, not in the real DB)

- The user: process Desktop/reel_videos/test.MOV and put the result in the same folder.
- Run with a separate DB and folder (`data/trial_test`), publishing forced to dry-run (the live
  config has YouTube on). Done in 80 s: 7:19 → 6:32 (47 s with nobody at the board and no
  sound removed). Output: `reel_videos/test_reel.mp4` and `test_reel_chapters.txt`.
- **Finding:** the audio has no speech: a steady room noise at about −45 dB for all 7 minutes.
  Whisper's voice detection found nothing, also after +25 dB of gain; without it Whisper only
  invented repeated words. So no transcript, no chapters, and the terms (T-1010) could not be
  checked on this video.
- **Gap:** such a job went to Done with no flag but `chapters_not_generated`. In live mode it
  would have been uploaded to YouTube without chapters. Proposed: put a job whose transcript
  is empty on hold ("no speech found") — waiting for the user's OK.

### 2026-11-21 14:48 — T-1010 English programming terms stay English in the transcript (D-062) — DONE

Resumed after the pause below (the user: "continue T-1010"; re-processing job 2: "no need, I
will bring a new video").

- `[stt] hotwords` is now off by default (schema, example and live config): the prompt reaches
  only the first 30-second window, because in every window it cut the speech by 10-25 %.
- New spellings from the trial: "հեշպունթյա", "հեշվունթյա" (hash function), "հեշտեբել",
  "հեշտ հեպլ", "hash-table" (hash table), "դատա ստրուկտուր", "data-structure",
  "կլեկշոն" (collection), "կի վալու", "կի վելյու" (key-value), "լոտ" (slot).
- On the trial clip the fixer now makes 40 fixes (was 38). All 40 are right; no Armenian word
  was changed. Left in Armenian letters: "դոբեկտի" (two words run together), "կի վալուզ".
- D-062 written; TASKS T-1010 DONE; 9 new test cases in `tests/unit/test_terms.py`. Full suite: 1310 passed (-W error), ruff clean.

### 2026-11-21 14:35 — T-1010 English terms in the transcript (user request) — IN PROGRESS, paused by the user

The user: Whisper writes English programming terms in Armenian letters ("դիկշնարի", "հեշտեբլ").
They must stay English, in Latin letters, spelled right, never translated.

**Done (committed as work in progress, tests: 1301 passed, ruff clean):**
- `src/reel/stt/terms.py`: `TermFixer` turns Armenian-letter spellings into the English term and
  keeps the Armenian ending after a hyphen ("դիկշների" → "dictionary-ների"); letter folding for
  long spellings, exact match for short ones; multi-word terms word by word. 32 unit tests.
- `transcribing.py` runs the fixer (`[stt] fix_terms = true`), records `term_fixes` in the details
  and metrics. `whisper.py` drops U+FFFD and segments that repeat the prompt.
- The chapter-title prompt asks for terms in Latin letters.

**Trial on 4 min of job 2 (data/trial/terms/, local):**

| Variant | Words | Note |
|---|---|---|
| v0 no prompt | 294 | full speech; with the fixer 38 terms fixed — the best |
| v1 initial_prompt | 299 | the same as v0; the prompt only reaches the first 30 s window |
| v2 hotwords long | 246 | speech lost |
| v3 hotwords example | 264 | speech lost |
| v4 hotwords short | 260 | speech lost + a "hash-fu, hash-fu…" loop |
| final (hotwords ~200 tokens) | 220 | 25 % lost: the prompt eats the 448-token window |

**Decision so far:** hotwords off; the fixer does the work. The live `config/reel.toml` already
has `hotwords = false`; the schema default and the example config still say `true`.

**Next (when the user resumes):**
1. Schema default `hotwords = False`, example config, tests (`test_whisper_options.py`).
2. Add the missed spellings: "հեշպունթյա(ն)", "հեշվունկթյա", "հեշտեբել", "հեշտ հեպլ", "կլեկշոն",
   "դատա ստրուկտուր", "դոբեկտ", "կի վալու", "մյութբլ", "սայզ", "բակետ", "սլոթ".
3. D-062 in DECISIONS, T-1010 DONE, commit.
4. Restart the production `reel serve` (it runs the old code; check for running jobs first).
5. Ask the user: re-transcribe job 2 (already on YouTube)? The external test instance runs the
   old code; restarting it changes the tunnel URL.

### 2026-11-20 20:45 — Review of Codex's external test (tools/public-test, authorized by the user)
- **Setup checked:**
  - a separate Reel copy on 127.0.0.1:8091 with its own database, recordings, groups and consent files, behind a Cloudflare Quick Tunnel;
  - YouTube live (unlisted); Slack and Classroom dry-run;
  - the supervisor, tunnel and server processes from `processes.json` are alive;
  - production source is untouched (`git status` clean).
- **Behaviour seen:**
  - The consent gate holds: the review file's empty roster gives `unknown`, so every test video waits for "Ընդունել և շարունակել". No consent is made up.
  - Client addresses arrive through the tunnel (X-Forwarded-For from 127.0.0.1), so the login throttle sees real addresses.
  - Groups typed there are added to the test registry only (D-061; "Math" was added).
- **The only real upload so far:**
  - test job 1, group "Math", 9.9 MB, 19 s long;
  - the browser upload took about 3.6 s through the tunnel (16:35:15.6 → 16:35:19.1 UTC), about 2.8 MB/s;
  - Import rejected it in 0.35 s: "too short: 19.0 s (minimum 300.0 s)" (`ingest.min_duration_minutes = 5`);
  - so there is no upload-to-YouTube time yet.
- **Points for the user:**
  - the friend must upload a lesson of at least 5 minutes;
  - `C:\Users\User\Desktop\warehouse\reel-test-access.json` holds the test password inside another git repository and is not ignored there;
  - the quick tunnel exposes a login page to anyone with the URL until `stop.ps1` runs;
  - the test copy shares the YouTube channel and its daily quota with production (one production upload was made today).

### 2026-11-20 20:38 — First real YouTube upload: job #2 is on the channel (unlisted)
- **The user:** the processed video should go to the friend's channel now; the playlist does not matter.
- **Why job #2 had stopped again:**
  - the `reel serve` the user started at 19:21 ran the code from before T-1008;
  - from 19:41 it rejected every config change ("web.tls: unknown setting"), so it never saw the "AI 2.6" group or `youtube.mode = "live"`;
  - at 20:31 it held the job at Chapters again.
- **Done:**
  - stopped that process and started `reel serve --host 127.0.0.1` with the current code (detached; HTTPS on this computer only, until the user opens the firewall);
  - released job #2 (`--by claude`): Chapters succeeded;
  - Consent could not find `config/consent.json` and was retried; the last try was run at once with `reel retry`; then it held (`consent_unreachable`);
  - accepted with `reel release 2 --accept --by claude`, noting the user's instruction. No consent data was made up.
- **Result:** Done at 20:38. YouTube **https://www.youtube.com/watch?v=fx4eCGpjMR0**, unlisted (no `youtube_not_unlisted` flag), on the channel "ReelProject" of the signed-in account, with no playlist (flag `youtube_no_playlist`). Slack and Classroom were skipped (dry-run); the local Academy Library published it.
- **Found on this computer** (not made by Claude): `tools/public-test/` and `C:\Users\User\Desktop\warehouse\reel-test-access.json`, made 20:12–20:32, probably by another agent.
  - They run a separate Reel copy (own database `tools/public-test/runtime/`, port 8091, YouTube live, test account "tester").
  - That copy is reachable from the internet through a Cloudflare quick tunnel (`*.trycloudflare.com`).
  - The access file in the warehouse folder holds the test password and is **not git-ignored** in that repository.
  - Left running; reported to the user.

### 2026-11-20 20:30 — YouTube publishing turned on (the user: "Այո, միացրու YouTube-ը")
- `config/reel.toml`: `[youtube] mode = "live"`. Slack, Classroom and staff notifications stay in dry-run. Privacy stays unlisted (D-017).
- **Checked before:**
  - `reel auth status --check`: both YouTube permissions are granted, and the access token was refreshed.
  - `reel config check`: OK. A group without a playlist is now a warning, not an error, because the video is still uploaded (flag `youtube_no_playlist`) and typed groups start with empty IDs (D-061). Test: `test_config_cli.py`.
- **What happens to job #2 when Reel starts:**
  - Chapters runs.
  - Consent holds the job for a staff release, because there is no `config/consent.json`.
  - After **Թողարկել** in the dashboard, Upload sends the video to YouTube as unlisted, without a playlist.
- **Known limits** (docs/setup/google.md):
  - An unaudited API project may get its videos locked to private (flag `youtube_not_unlisted`; Google's free audit form fixes it).
  - An *External* app in *Testing* status loses access after 7 days; *Publish app* avoids it.
  - Uploads use most of the free daily quota.
- Committed with the check change.

### 2026-11-20 20:25 — T-1009 Why the video did not reach YouTube; typed groups added; transcript off the page (D-061) — DONE
- **The user's question:** why does the processed video not go to YouTube?
- **Found** (real database, `reel status 2`, `reel auth status`):
  1. Job #2 ("AI 2.6 · lesson 15") was on hold at **Chapters**, two stages before YouTube: "Set the group of this recording". The group was set, but `groups.toml` had only "Python 12". The upload form accepts any typed name since the form fix, and nothing added it.
  2. `[youtube] mode = "dry-run"`: by the user's own rule (local only until they say), nothing is ever sent.
  3. Google had granted only `youtube.upload`, not `youtube` (playlists). The user signed in again during the work, and now both are granted.
  4. There is no `config/consent.json`, so every recording waits for a staff release before upload (D-019).
- **The user's answer:** do it, add any typed group at once, and remove the "Տառադարձում (մաքրված տեսանյութի ժամանակներով)" section from the page.
- **Done:**
  - `config/groups.py`: `add_group` and `missing_group_message`. `web/forms.py`: `ensure_group`, used by the upload form and by "set the group", only after the rest of the form is valid.
  - Chapters, Consent and Upload name a group that is not configured.
  - `reel auth status` lists each service's permissions and warns about a missing one.
  - The transcript section was removed from `recording.html` and `detail.py`, with its CSS.
  - "AI 2.6" was added to the real `config/groups.toml`, with empty IDs. Job #2 was released with `--by claude` and is **queued at Chapters**: it goes on when Reel starts.
- **Not done:** YouTube stays in dry-run. Going live publishes for real; it waits for the user's explicit say, the playlist ID and the consent registry.
- **Tests:** `test_groups_add.py` (10: kept text, existing name or alias, Armenian and quotes, no file, no final line break, broken file and bad name change nothing, parallel adds write once, messages). Updated: `test_upload.py` (a wrong form adds nothing; added once; found by another spelling), `test_web_detail.py` (added on "set the group"; no transcript on the page), `test_chapters_stage.py` (the message names the group), `test_google_auth.py` (a missing permission).
- **Checks:** full suite 1264 passed with `-W error`; ruff check and format are clean (184 files).

### 2026-11-20 19:50 — T-1008 The dashboard on the local network over HTTPS (user request, D-060) — DONE
- **Request:** "make the HTTPS setup so staff can sign in from the network".
- **How:** see D-060. Reel serves HTTPS itself, with a certificate from its own CA. The CA is limited by name constraints to this computer's names and private addresses. The keys are encrypted, and their password is in Windows Credential Manager.
- **Code:**
  - `src/reel/web/tls.py` (new): names and addresses of this computer, the CA, the server certificate, renewal, `read`, `uvicorn_options`;
  - `reel cert [--name] [--ip] [--new-ca] [--show]` in `web/commands.py`; `reel serve` passes the TLS options and logs the address staff open;
  - `Runtime(tls=…)`; `[web] tls` with a check that it has an `https://` `base_url`;
  - `scripts/network.ps1 allow|remove|status`: a Windows Firewall rule for private networks only, and a warning about rules that block Python.
- **Dependency:** `cryptography` 50.0.2 (Apache-2.0 OR BSD-3-Clause), with `cffi` and `pycparser`; in `pyproject.toml` and THIRD_PARTY.md.
- **Tests:** `test_tls.py` (14), with real TLS handshakes checked by OpenSSL:
  - every name and address opens; other names are refused;
  - **the CA cannot sign another site** (`X509_V_ERR_PERMITTED_VIOLATION`);
  - keys are encrypted; a renewal keeps the CA; a new CA when asked, after a rename, or near its end;
  - validity, the key password in the secret store, the serve checks, `reel cert`;
  - the dashboard answers over HTTPS with a `Secure` cookie, and plain HTTP gets nothing.
- **On this computer:**
  - `reel cert` made the CA (until 2036-11-17; SHA-1 thumbprint `AA:04:0C:14:35:0B:D1:DB:49:25:A3:F9:7E:A2:E1:C6:F2:7A:67:6D`) and the certificate (until 2027-12-22) for `desktop-3gig9mv`, `.local`, `localhost`, 127.0.0.1, 172.23.208.1 and 192.168.33.109.
  - `config/reel.toml`: `host = "0.0.0.0"`, `tls = true`, `base_url = "https://desktop-3gig9mv:8080"`.
  - Smoke test on 127.0.0.1:8093: all five names answered 200 over TLS 1.3 with a `Secure` cookie; `example.com` was refused.
- **Not done by the agent** (the firewall change was refused to the agent; it needs the user's administrator rights):
  - `scripts\network.ps1 allow` as administrator;
  - installing `reel-ca.crt` on this computer and on the staff computers.
  - The script was parse-checked only.
- **Guide:** `docs/setup/network.md`; README and `config/reel.example.toml` updated.
- **Checks:** full suite 1254 passed with `-W error`; ruff check and format are clean (183 files).
- **Follow-up (20:05, the user: "commit, and do the remaining steps"):**
  - Committed `3e8c007`.
  - `network.ps1 allow` ran elevated after the user approved UAC, and added the private-only rule.
  - `status` then showed that the "Picsart Academy" Wi-Fi is **Public**, so that rule does not apply there. Making the shared Wi-Fi private would loosen the whole laptop's firewall on a network that students use too.
  - So `allow -Subnet <range>` was added: only Reel's port, only from that range (here 192.168.32.0/22, from 192.168.33.109/22), and the network stays public. The guide now says to open the firewall before Reel listens on 0.0.0.0. Committed `5ff0ebd`.
  - Running `allow -Subnet` was refused to the agent, because it opens a port on a public network. The user runs it.
  - Installing the CA in the user's root store needs a Windows confirmation dialog, which the agent's shell cannot show ("UI is not allowed"). The user installs it.
  - Reel is not started yet: it should start after the firewall rule exists.

### 2026-11-20 18:55 — T-1007 reviewed (DONE); the project became a local git repository
- **GPT's work (handoff/T-1007.report.md):** `style.css` rewritten as one dark palette in `:root` (41 variables, the user's 14 with their exact values). `base.html` got `nav-link` / `is-active` (the active page is underlined in magenta). `recording.html` and `metrics.html` got `table table-panel` on their table cards. Nothing else changed.
- **Review:**
  - no hex, `rgb()`, `url()` or `@import` outside `:root`; no inline styles or scripts; CSP unchanged;
  - templates: only class attributes changed; `request` is always in the template context (`render` → `TemplateResponse(request, …)`);
  - links use the soft pink `#FFA3F9` (10.66:1); primary buttons are white on `#D500C8` at 16 px / 650 (4.50:1); status colours are separate from the brand; the report's contrast table was spot-checked.
- **One fix by Claude:** the disabled ↑ ↓ buttons looked almost the same as the enabled ones (`#D0D0D0` on `#333333`, against `#FCFCFC` on `#333333`). Disabled controls are now grey `#888888` on the card colour `#1F1F1F` (4.65:1) with a dim border.
- **Checks:** full suite 1240 passed with `-W error` (4 min 11 s); after the fix, the web tests passed again (107); ruff check and format are clean (180 files).
- **Git (the user asked to commit the work):** the folder was not a repository, so a local one was created (`main`, no remote; nothing pushed). Before the first commit, the 230 files to commit were scanned: they hold no client id or secret, no `.env` value and no email. `.env`, `config/google-client.json`, local config, `data/`, `samples/`, `logs/`, `tools/` and `models/` are ignored.

### 2026-11-20 18:18 — T-1007 Dashboard design handed to GPT
- The user asked for a new dashboard design, given as a design prompt (dark charcoal surfaces, magenta actions, mint accents, inspired by picsartacademy.am), and for the work to go to GPT. This ends the GPT pause for this task.
- `handoff/T-1007.md` holds the user's text verbatim plus Reel's constraints:
  - only `style.css` and the classes in the templates may change;
  - the CSP stays: no inline styles, no external fonts or CDNs; system fonts with Armenian;
  - field names, ids and tested markup stay as they are;
  - dark only;
  - contrast: `#D500C8` text on `#111111` is 4.19:1 and on `#1F1F1F` 3.66:1, so links use `#FF3DF3` (6.49:1); white on `#D500C8` is 4.50:1, so labels are 16 px semibold. Computed, not estimated;
  - status colours distinct from the brand;
  - a preview on port 8091 with a throwaway database.
- Claude reviews the result and runs the full suite before it counts as done (D-031).

### 2026-11-20 18:08 — The user's review of the first 15 minutes: two fixes (D-059, D-058)
- **Trial:** the first 15 minutes of IMG_0359, through the whole pipeline in its own data folder (`data/trial15`), finished Done in 18 min. The result is `Desktop/reel_videos/IMG_0359_first15min_reel.mp4`.
- **1. Lesson start (D-059).** The user: the first 2:48 were lesson, and must not be cut. A lesson may start without any announcing phrase when the recording starts with the teaching.
  - The LLM had named the line "բարձը, իմա որինակ ... եկեք բերենք" ("now let's take an example") as a greeting.
  - **Fix:** the LLM's line must hold a configured phrase, both for the start and for the end. That line holds none, so it is rejected, and none of the first 15 minutes holds a start phrase.
  - **Tests:** `test_boundaries.py`: the real case, the LLM alone never cuts, and paraphrases count once they are in the phrase lists.
- **2. Framing (D-058).** The user: keep the logic, but the crop must include the whole board.
  - **Fix:** the board surface is grown from the work box (brightness, row and column coverage, rails crossed, poles not followed). The crop goes around the board and the work. The margin is now 3 %.
  - **Tests:** `test_framing.py` +4: board found across a rail and not along a pole, a white board on a white wall not found, the union of board and work, and the board of a synthetic lesson.
  - **On the real lesson:**
    - first 15 minutes: the board found at x 0.091–0.935, y 0.239–0.868 (all three panels), crop 1.12×;
    - second part (IMG_0360): the same board found (x 0.091-0.935, y 0.235-0.868), crop 1.12x; the whole board, with "LIFO/FILO" and "stack/call frame", stays in.
- Full suite: 1240 passed with `-W error`; ruff is clean. `reel serve` restarted.
- **Re-run with both fixes (18:28):** the same clip, Done in 19.5 min (import 7 s, transcription 8.8 min, cleaning 9.2 min, chapters 1.3 min).
  - Start and end not cut: both flags set; the LLM was asked twice, and both answers were rejected. Only 12 s of silence were removed (15:00 → 14:48).
  - Framing 1.12×, crop `[252, 228, 3434, 1930]` of 3840×2160; the whole board is in. 3 chapters.
  - Result: `Desktop/reel_videos/IMG_0359_first15min_reel_v2.mp4` (166 MB), for the user to view.

### 2026-11-20 17:13 — T-1006 Board framing (user request, D-058) — DONE, the user reviews it
- **Request:** as the last step of the video processing, bring the video's edges close to the board with a small margin. A board, a projector or a whole wall: find the outermost points where work is done.
- **How:** see D-058. Traces of work (writing or slide changes) are found on the board-check pictures, with people masked out, and give the work box. A margin is added, the box is widened to 16:9, and the zoom is at most 2×. Parts from one camera get one crop. The render crops before the scaling, in the same encode (CPU `crop`, Quick Sync `vpp_qsv`).
- **Code:**
  - new `src/reel/cleaning/framing.py`;
  - `cleaning/presence.py`: the detector keeps the people's boxes, and the board pass collects the work;
  - `media/render.py`: per-part crops;
  - `stages/cleaning.py`: the framing step, `cleaning.json` → `framing`, metric `framing` (zoom per part);
  - `config/schema.py` and `reel.example.toml`: `[cleaning.framing]`.
- **On the real lesson** (the T-1002 recording; previews in `data/trial/framing/`):
  - **First version:** the box reached the picture's bottom, because a student's head near the camera, missed by the detector, counted as work. The zoom was 1.18×.
  - **Fix 1:** traces touching the bottom edge are left out. The zoom became 1.31×, but the outer-1 % trimming cut "LIFO/FILO" and "stack/call frame" at the right edge of the board.
  - **Fix 2:** the trimming was replaced by groups. Groups under 2 % of the work are dropped (a laptop on the table was 0.2 %).
  - **Result:** part 1 alone would get 1.13×, part 2 1.08×. Together (one camera), both parts get 1.08×.
  - The whole board, with all its writing, stays in the picture. The gain is small here because the board takes about 87 % of the width and all three panels were used.
  - A full render with the crop was not run on the real lesson (50 min). The crop was checked on still frames of the real lesson, and on real renders of a test video with both encoders.
- **Tests:**
  - new `test_framing.py` (15): writing found, people masked, a head at the bottom, a small laptop, a moved camera, no work, the crop's margin, shape, zoom limit and edges, the union;
  - `test_render.py` +3: the crop in the CPU and GPU graphs, and real renders with libx264 and Quick Sync (the white half of a test picture);
  - `test_clean_stage.py`: the framing summary and metric.
  - Full suite: 1234 passed with `-W error`; ruff is clean.
- `reel serve` restarted with this code; it applies to new recordings and to re-runs from Cleaned.

### 2026-11-20 16:24 — Google connection: client installed; `reel auth google --only youtube`
- The user's friend created the OAuth client. Its id and secret were saved as `config/google-client.json` (git-ignored), and `.env` points to it (`GOOGLE_CLIENT_SECRETS_FILE`). Reel loads the file without errors. Publishing stays dry-run, as the user asked.
- The first sign-in ended on Google's page "Сервис недоступен" ("This service isn't available for your account"). Reel asks for YouTube and Classroom access in one request, and Google refuses the whole request when one service is switched off for the account.
- **Fix:** new option `reel auth google --only youtube|classroom` (`SERVICE_SCOPES` in `auth/google.py`). The setup guide explains the page and the other causes: an account whose administrator turned YouTube off, a child account, or the wrong account chosen.
- **Tests:** `test_google_auth.py` +3 (the scopes asked with and without `--only`). 24 auth and CLI tests pass; ruff is clean.
- **Waiting for:** the sign-in with the channel's account, and the playlist ID for each group.

### 2026-11-20 16:07 — User request: the cleaned video is watched in the dashboard and never lost while local (D-057) — DONE
- **Retention:** a Done job's working files (with `cleaned.mp4`) and its source recordings are removed only when the video is really on YouTube (a live published row with a video id).
  - In dry-run they are kept, and `reel cleanup` lists them as "Kept: … the video is not on YouTube, so this PC has the only copy".
  - Old transcript rows still go after 365 days; title, description and chapters stay.
  - While checking this, one mistake of the first version was fixed: it reported a kept source file that did not exist. Each existing file is now checked first.
- **Recording page:** a "Տեսանյութ" card with a player (`GET /recordings/<id>/video`; Range requests, so seeking works) and a "Ներբեռնել (MP4)" link (`?download=1`, named after the title, made safe for Windows). Staff login is required. A note says when the video is only on this PC.
- **Code:**
  - `retention.py`: `on_youtube`, `Report.kept`, `plan(…, kept)`;
  - `queue/admin.py`: prints "Kept";
  - `web/detail.py`: `video_path`, `video_file_name`;
  - `web/app.py`: the video route, and the page context `video` and `on_youtube`;
  - `recording.html`, `style.css`.
  - The CSP is unchanged: `default-src 'self'` covers the video.
- **Tests:**
  - new `test_web_video.py` (5): player and 404, Range 206, download name, the note when the video is on YouTube, login;
  - `test_retention.py`: a job helper with the YouTube state, a test of the kept only copy, and "Kept" in the command's output.
  - Full suite: 1213 passed with `-W error`; ruff is clean.
- The running `reel serve` was restarted with this code.

### 2026-11-20 15:46 — User request: typed group and lesson fields — DONE
- **Upload page and the recording page's identify form:**
  - **Group:** typed, with the groups from groups.toml as suggestions (`<datalist>`). A typed name that matches a group or alias (case, spaces, `_` and `-` ignored) is saved under the configured name. Any other name is kept as typed: such a group has no playlist, Slack channel or Classroom course.
  - **Lesson number:** this field could not be typed into. It was `type="number"`, and the Armenian keyboard layout installed on this PC most likely types letters on the number row, which a number field refuses without any message. The field is now plain text (`inputmode="numeric"`). The server checks it and explains what is wrong ("type the digits with the Latin keyboard").
- **Code:** new `src/reel/web/forms.py` (`parse_group`, `parse_lesson`), used by `web/uploads.py` and `web/app.py` (identify); templates `upload.html` and `recording.html`.
- **Tests:**
  - new `test_web_forms.py` (20 cases);
  - `test_upload.py`: page fields, wrong values (Armenian digit key, empty or too long group), and the typed group matched or kept;
  - `test_web_detail.py`: typed group matched or kept, and an Armenian "digit".
  - 94 web and end-to-end tests pass; ruff is clean.
- The running `reel serve` was restarted, so the changes are live at http://127.0.0.1:8080.
- **Not done yet:** the Slack and YouTube credential fields. The user first asked for the steps to get the YouTube access. Publishing stays dry-run (local only) until the user says otherwise.

### 2026-11-20 15:04 — T-1002 Real recording trial — RUN DONE, waiting for the user's review
- **Input:** one real lesson in two parts (`samples/IMG_0359.MOV` + `IMG_0360.MOV`, 4K, 13.7 GB, 1:40:20 together).
  - It ran as group "Trial", lesson 1, in a separate data folder (`data/trial`), so the real database was not touched.
  - The publishing was dry-run.
- **Result:** the job reached Done.

| Stage | Time (awake) | Notes |
|---|---|---|
| Imported | 29 s | SHA-256 of both parts 16 s; audio of the parts joined |
| Transcribed | 69.1 min | 0.69 × real time; faster-whisper large-v3-turbo int8; 521 segments |
| Cleaned | 51.5 min | lesson boundaries 52 s (1 LLM call), board check 4.8 min, **silence check 30.8 min**, render 15.1 min (h264_qsv, 1920×1080, −16.2 LUFS, 1.08 GB) |
| Chapters | 8.3 min | 8 chapters; 40 LLM calls, 36 k prompt tokens + 1.8 k output tokens |
| Consent, Uploaded, Posting links | 1 s | dry-run; `youtube_no_playlist` because the trial group has no playlist |
| **Work in total** | **2 h 9.5 min** | 1.29 × the lesson's length |
| Wall time | 12 h 50 min | includes 10.5 h of sleep: the battery ran out (D-056), and attempt 1 of Cleaned was lost |

- **Cuts:** 38 s were removed, from 1:40:20 to 1:39:42:
  - four silences over a static picture, 7–12 s each, at 11:26, 30:58, 59:34 and 1:09:03 of the original;
  - 1.4 s after the closing "thank you".
  - **No start was cut:** the lesson start was not found (flag `lesson_start_not_found`). The LLM's one answer quoted a word that was not in the line it named, so it was rejected (D-039).
  - **No break was cut:** somebody was at the board in 7,073 of 7,077 pictures. The break is between the two files.
- **Chapters (cut video):** 0:00, 6:28, 19:05, 31:27, 35:44, 41:57, 54:10, 1:07:18. The topics are hash tables, collisions and hash functions, user-defined functions and objects, the sum of two numbers, memory layout and the stack, recursion and call parameters, local and global variables, and returned lists.
  - The titles carry the transcript's misspellings of technical terms, such as hash table, function and object. The fix is the group's `vocabulary` in `groups.toml`, which goes into Whisper's prompt.
  - The last chapter covers 32 min (1:07:18 to the end).
- **For the user's review** (`data/trial/jobs/1/`):
  - `cleaned.mp4`: the cut video;
  - `metadata.json`: title, chapters, description;
  - `thumbnail.jpg`;
  - `cutlist.json` and `cleaning.json`: the cuts and their reasons.
- **Proposed follow-ups, waiting for the user's OK:**
  - **(a) Silence check.** It is the slowest part of cleaning: 30.8 min, because the whole 4K picture is checked for stillness. Checking the picture only inside the silent stretches would save about 25 min per lesson.
  - **(b) Vocabulary.** Add a vocabulary of technical terms to the real groups.
  - **(c) Start phrases.** Add phrases for the lesson start, after the user says how this teacher starts a lesson.
- NFR-1 updated in the verification section above.

### 2026-11-20 14:35 — T-1002 finding: the PC's sleep during the work (D-056) — FIXED
- **What happened in the trial:**
  - The laptop ran on battery. At 03:35 the battery was empty, and Windows put the PC to sleep in the middle of the Cleaned stage (System log event 42, "Sleep Reason: Battery"). It woke at 14:02, when it was plugged in.
  - The stage timeout (6 h) counted the 10.5 hours of sleep as work. So the stage was stopped as "timed out after 21600 s" and run again as attempt 2. The import and the transcription were kept.
  - The budget check saw 709 min of processing and warned.
  - Cause: on Windows, `time.monotonic` (QueryPerformanceCounter) goes on during sleep.
- **Fix:**
  - New `src/reel/power.py`:
    - `awake_clock` uses QueryUnbiasedInterruptTime, which leaves out sleep;
    - `keep_awake` sets `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` for the time a stage runs.
  - `queue/worker.py`:
    - the stage timeout follows the awake clock, and heartbeats still follow `time.monotonic`, so the lease is renewed as soon as the PC wakes;
    - the time asleep is saved as `suspended_seconds` on the stage run and logged;
    - each stage runs inside `keep_awake` (setting `workers.keep_awake`, on by default);
    - a claim no longer recovers the expired leases of the worker's own running slots (`JobQueue.claim(keep=…)`). Before, the second slot could take a job away from a stage that was still running after a long sleep.
  - `metrics.py`: processing time and the budget leave out `suspended_seconds`. The time from file to links still includes it.
- **Config:** `keep_awake` was added to `config/reel.example.toml` only.
  - In `config/reel.toml` it was added and then taken out again: the trial process was still running the old code, which rejected the unknown key. That process logged one error and kept its previous settings, so the trial was not affected. The default (`true`) applies without the line.
- **Docs:** D-056 in DECISIONS.md; a README troubleshooting row (keep the PC plugged in); limitation 12 in the verification section above.
- **Tests:** new `test_power.py` (8 tests), plus 2 in `test_worker.py` (a stage that "sleeps" past its timeout succeeds and records the time asleep; set and clear of the stay-awake request in the stage thread) and 1 in `test_queue.py` (`keep`). The full suite passes: 1183 with `-W error`. ruff is clean.
- **Not prevented, and rightly so:** a low battery, a closed lid or the power button still put the PC to sleep.

### 2026-11-20 02:57 — T-1005 Final verification — DONE (to be refreshed after T-1002 / T-1003)
- Every PRD item (FR, NFR, UJ, C, M) was checked against the code and the tests. The result is the "Final verification (T-1005)" section near the top of this file, with 11 known limitations.
- Full suite 1172 passed with `-W error` (7.4 min, with the trial running at the same time); ruff check and format clean (173 files).
- Found while checking: the fresh-install check of T-1004 had left a `build/` folder, so ruff scanned 92 copied files. The folder was removed; it was ignored by git anyway.
- Remaining:
  - T-1002: the trial on the real lesson runs in the background; its results table follows, and the user reviews the cuts and chapters;
  - T-1003: the live publishing check needs the academy's accounts.

### 2026-11-20 02:48 — T-1004 Documentation — DONE
- **README.md rewritten:**
  - requirements, install, configuration (files, dry-run, the `REEL__…` overrides);
  - running (`reel serve`, autostart);
  - daily use (adding recordings, following the work, what to do when Reel needs staff, the two rules for a quiet empty board, metrics);
  - all commands; a troubleshooting table; documents; folders; development.
- **THIRD_PARTY.md:** every Python package from the installed metadata, with its version and licence; FFmpeg (GPL-3.0 essentials build, run as a separate program), Ollama (MIT), the models (Whisper MIT, YOLOX Apache-2.0, Gemma under the Gemma Terms of Use, with how to switch to an OSI-licensed model); the free services. Reel's own licence is still open.
- **CHANGELOG.md:** 0.1.0.
- **ARCHITECTURE.md:** the directory layout now matches the code.
- **Integration guides:** `docs/integrations/` (Core Engine consent, Academy Library, Reel API) and `docs/setup/` (Google, autostart).
- **Fix:** `numpy` and `onnxruntime` are used directly but were only installed through faster-whisper. They are now declared in `pyproject.toml`.
- **Fresh setup check:** a new venv inside the project (`py -V:3.14 -m venv`, then a normal `pip install ".[dev]"`, not editable) installed without errors.
  - The installed package has all templates and static files.
  - `reel --version` and `reel config check` work. The check's only warning: `config/consent.json` is missing, which the user fills in.
  - 56 web, CLI and upload tests passed against the installed package.
  - The venv was then deleted.
- The model downloads (FFmpeg, Whisper, Ollama) were not repeated: they are the same scripts that installed them before.
- Next: T-1005 — Final verification (T-1002 keeps running in the background).

### 2026-11-20 02:37 — T-903 Deployment packaging — DONE (one step for the user)
- `scripts/autostart.ps1` (install, start, stop, status, uninstall), `docs/setup/autostart.md`, and a README line.
- `reel` works without a console: under `pythonw` its output goes to `os.devnull` (`cli.use_utf8_output`).
- No Docker image, with the reasons. Rules in D-055.
- **Checked:**
  - the script parses;
  - every task setting was built in memory with the real cmdlets (action, logon trigger, restart 3 × 1 min, no time limit, `IgnoreNew`, interactive, limited rights) without registering it;
  - `pythonw.exe -m reel status` ran without a console and exited with 0;
  - test `test_cli` +1.
- **Not checked:** registering and starting a temporary task on this PC was refused by the agent's permission system (a system change). **For the user:** run `powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 install`, then `… start` and `… status` once. After that, http://127.0.0.1:8080 should answer.
- Next: T-1004 — Documentation (T-1002 keeps running in the background).

### 2026-11-20 02:34 — T-902 Data retention — DONE
- `src/reel/retention.py`: `plan`, `cleanup` → `Report` (dry-run lists only; `cleanup` events; errors are reported, not fatal) and `daily`.
- `reel cleanup [--dry-run]` (in `queue/admin.py`); `reel serve` runs it daily (`Parts.cleanup`, `--no-cleanup`).
- The detail page checks that the thumbnail file exists.
- Q-11 uses the defaults. Rules in D-054.
- Tests: `tests/unit/test_retention.py` (6, fake clock):
  - only expired things go: old Done and very old Failed folders, the old transcripts and the stale unfinished upload; not newer, running or held jobs, a fresh upload, a folder that is not Reel's, or the titles;
  - dry-run deletes nothing and writes no events; a second run has nothing left to do;
  - `0` keeps everything;
  - sources only when enabled: a Done job's source, both parts of an uploaded lesson; not a Failed one, a recent one, or a file outside the watched folders;
  - the `reel cleanup` command; the daily loop; the detail page after its thumbnail was removed.
- `test_runtime` updated (cleanup thread).
- Full suite 1171 passed with `-W error`; ruff clean.
- Next: T-903 — Deployment packaging (T-1002 keeps running in the background).

### 2026-11-20 02:23 — T-806 Integration API for other teams — DONE
- T-806 was a GPT candidate, but GPT is paused (user, 2026-11-20), so Claude did it while the T-1002 trial runs in the background.
- `src/reel/web/api.py`: `/api/v1/recordings` (status, group and `updated_since` filters, id paging), `/api/v1/recordings/{id}` (with summary, description, chapters, removed time, consent result, publications) and `/api/v1/library`. Bearer tokens come from `REEL_API_TOKENS`.
- The session middleware leaves `/api/` alone (no cookie); unknown API paths answer JSON.
- Contract: `docs/integrations/reel-api.md`. Rules in D-053.
- Tests: `tests/unit/test_web_api.py` (6):
  - missing, wrong, non-Bearer and too-short tokens → 401, with no cookie;
  - no tokens → 503;
  - the exact documented keys of the list, the detail and the library;
  - dry-run links hidden; the transcript and the student id not exposed;
  - paging with `next_after`; the filters; 4 invalid queries → 400;
  - unknown paths answer JSON.
- Full suite 1165 passed with `-W error` (slower now: 7.5 min, because the trial runs at the same time); ruff clean.
- Next: T-902 — Data retention (the T-1002 trial keeps running in the background).

### 2026-11-20 02:12 — T-1001 End-to-end tests on synthetic recordings — DONE
- `tests/integration/test_e2e.py`: all seven real stages with real FFmpeg on a generated 2-minute lesson (chatter, lesson, a quiet empty board, lesson, chatter). Stand-ins only for the speech model (the scripted Armenian lesson), the LLM (scripted labels), the person detector, and, in UJ-5, the YouTube, Slack and Classroom services (the local fakes).
  - **UJ-1:** a file dropped into `inbox/Python 12/` goes through `Runtime` (watcher, workers, notifier) to Done. Group and lesson come from the path; the cut list is shorter than the original; the title is set; four dry-run publications are made; `cleaned.mp4` exists.
  - **UJ-3:** a student opted out → On hold at Consent (one staff alert, nothing published) → staff accept → Done; the decision is on the consent row.
  - **UJ-4:** the speech model crashes → retried → Failed (one alert) → fixed → re-run from Transcribe → Done; the import was not repeated.
  - **UJ-5:** live publishing to the fakes → Done. Staff report a wrongly cut quiet board part, the settings are changed and the job is re-run from Clean: cut list version 2 keeps the part; a new video is uploaded because the cleaned file changed; still one Slack message (updated with the new link), one active Classroom post (replaced) and one library entry.
- Found while writing UJ-5: a quiet empty board part is removed by the silence rule as well as by the empty-board rule, so keeping it needs both settings changed. This is the expected behaviour, and staff should know it (it will go in the user guide).
- Full suite 1159 passed with `-W error`; ruff clean.
- Next: T-1002 — Real recording trial.

### 2026-11-20 02:02 — T-901 All-in-one runtime and CLI — DONE
- `src/reel/runtime.py`: `Runtime` (notifier first, then watcher, workers, dashboard in a thread; `alive`, graceful `stop`), `Parts`, `process_alive` (OpenProcess, nothing is signalled), `gone_here`.
- `JobQueue.expire_leases_of`.
- `reel serve` runs everything (`--no-web|--no-watcher|--no-worker|--no-notifier`).
- `src/reel/queue/admin.py`: `reel status [job]`, `rerun`, `release [--accept]`, `cancel`, `retry`, audited with `--by`/`--note`.
- README updated. Rules in D-052.
- Tests:
  - `tests/integration/test_restart.py`, the acceptance test with real processes: Reel is killed in the middle of the clean stage (TerminateProcess; no clean-up). The second start finishes the job in a few seconds, not after the 300 s lease: import and transcribe ran once, clean twice, chapters/consent/upload/post once, and the `lease_expired` event is there;
  - `tests/unit/test_runtime.py` (10): process check; which workers are gone; a gone worker's lease ends and the job is claimed again at the same stage; everything starts together (the dashboard answers over HTTP, a stage runs) and stops gracefully (the job is given back, its run interrupted, all threads ended); `reel status` list, filter and detail; the job actions and their audit.
- Found while testing: on Windows the venv's `python.exe` starts the real interpreter as another process. The test therefore takes the PID from the job's lease, and killing the launcher also ended the interpreter (checked: no process was left).
- Full suite 1155 passed with `-W error`; ruff clean.
- Next: T-1001 — End-to-end tests on synthetic recordings.

### 2026-11-20 01:52 — T-807 Upload recordings through the dashboard — DONE
- `src/reel/ingest/upload.py`: `UploadStore` (create with checks; `writer` appends a piece only at the file's end, under a lock, with fsync; status, cancel, finish = move the folder and create the lesson) and `create_lesson` (one file, or the parts in `media_info["parts"]`; staff identification).
- `src/reel/web/uploads.py` (JSON API), `templates/upload.html`, `static/upload.js` (pieces, progress, ↑ ↓ order, retry with backoff, resume through localStorage), a "Վերբեռնել" link in the menu; CSP `script-src 'self'`.
- Config `web.upload_max_gb` (20).
- Import stage: lessons in parts (hash of all parts, validation of each, the minimum on the total, audio joined to exact durations, `media_info.parts`); staff values kept and only empty ones filled.
- `JobQueue.identify` now sets `set_by_staff`.
- The task's questions (size limit, who uploads, do form values win) were answered with defaults, because the user asked not to be asked. They are in D-051 and can be changed.
- Tests:
  - `tests/unit/test_upload.py` (17): refused inputs (empty, type, size, too many), a full disk, offsets (no double write, too big, unknown file, unsafe id), a dropped piece keeps its bytes and resumes, a too-big piece, safe names, the page and login, CSRF, wrong form values, a two-part upload with a lost answer resent, finishing too early, the stored files, recording and job, finishing twice, one file, giving up;
  - a 128 MB file in 8 MB pieces: peak memory stays about one piece and the content is exact. A probe showed the peak stays about one piece for 32 MB and 128 MB files; without `gc.collect()` the test client keeps garbage around;
  - `tests/integration/test_import_stage.py` (+3, real FFmpeg): two parts become one recording with the joined hash, durations and exactly matching audio length; staff values are kept and only empty ones are filled; a broken part fails the lesson.
  - `test_web_auth` was updated for the CSP.
- Full suite 1144 passed with `-W error`; ruff clean.
- Still to do: a check in a browser with a real multi-GB iPhone file. The JavaScript is not covered by the Python tests; its server side is.
- Next: T-901 — All-in-one runtime and CLI.

### 2026-11-20 01:38 — T-805 Metrics, cost and budgets — DONE
- `src/reel/metrics.py`: `JobMetrics`, `load_job_metrics`, `summarize` → `Summary` (`to_dict`), `check_budget`.
- `reel.llm.MeteredLLM` meters the LLM, and the clean and chapters stages store `metrics.llm`.
- Worker: `_check_budget` after every stage attempt.
- `notify/staff.py`: an alert for `budget_exceeded`.
- Web: `/metrics` and `/metrics.json` (`templates/metrics.html`, a "Չափումներ" link in the menu), and the "Ծախս և ժամանակ" section on the recording page.
- Q-3 ("same evening") uses the default cutoff of 23:59 until the user answers.
- Rules in D-050.
- Tests: `tests/unit/test_metrics.py` (6):
  - the summary on 4 seeded jobs with known times: every number;
  - an empty period;
  - the budget is flagged once;
  - the worker checks the budget after a stage;
  - the LLM meter;
  - the page, the JSON and the detail section.
- Also `test_notify` +1 (the budget alert).
- Full suite 1124 passed with `-W error`; ruff clean.
- Next: T-807 — Upload recordings through the dashboard.

### 2026-11-20 01:29 — T-804 Recording detail and staff actions — DONE
- `src/reel/web/detail.py`:
  - `load_detail`: the facts, stage runs, cut-list bar segments with Armenian reasons and removed time per reason, transcript preview (clean, else raw; 60 lines), metadata, consent checks, the four publication rows (`safe_link`), events, and which stages can be re-run;
  - `thumbnail_path` (only inside the data folder).
- `templates/recording.html` and its CSS (SVG cut bar, light and dark).
- Routes in `app.py`: `GET /recordings/<id>`, `/recordings/<id>/thumbnail`; `POST …/release|cancel|identify|rerun|retry`.
- `JobQueue.identify` and `EventKind.IDENTIFIED`; the Armenian texts are in `texts.py`.
- Editing the title or description is not one of the T-804 actions, so a re-run cannot overwrite a staff edit (the note from earlier).
- Rules in D-049.
- Tests: `tests/unit/test_web_detail.py` (16):
  - the whole page with all records: an escaped transcript; no `javascript:` link; the thumbnail is served;
  - a thumbnail outside the data folder → 404;
  - accepting a consent hold → upload, written on the consent row and the event with the note; "check again";
  - cancel (a second cancel → 409);
  - identify → the values, the flags removed, an identified + released event, the staff record;
  - 4 wrong identification inputs change nothing; a running job cannot be identified;
  - re-run from a stage (also one beyond the reached stage, and an invalid stage → 409);
  - retry now;
  - CSRF and login needed; an unknown recording → 404;
  - the identification flags match the identifier's.
- Full suite 1117 passed with `-W error`; ruff clean.
- Next: T-805 — Metrics, cost and budgets.

### 2026-11-20 01:18 — T-803 Recordings dashboard — DONE
- `src/reel/web/views.py`:
  - `recordings_page`: one query with the latest cut list per job, status counts, status and group filters, 50 per page, groups from the database and `groups.toml`;
  - `pills` (the 7 stage pills: done / running / queued / retry / held / failed / cancelled / pending), `recording_title`, `clock_text`, local times.
- `templates/recordings.html` replaces the start page: status counts as filters, a filter form, the table and pages; it reloads every 30 s (`meta refresh`, no JavaScript). `texts.py` has the Armenian flag and pill names; the CSS has pills, badges, flags and a table that scrolls on small screens.
- Each row links to `/recordings/<id>`; that page comes with T-804.
- Tests: `tests/unit/test_web_dashboard.py` (12):
  - jobs in every status: order, titles, an escaped file name, durations, hold message, errors, waiting retry, flags (an unknown code as it is), all status labels, and the pills of a done and a held job;
  - pill states for each status;
  - filters by status, group, no group, an invalid status; the empty message;
  - pages;
  - login needed; viewing changes nothing.
- `test_web_auth` was updated for the new start page.
- Full suite 1101 passed with `-W error`; ruff clean.
- Still to do: a manual look in a browser. `reel user add`, then `reel serve`, then open http://127.0.0.1:8080.
- Next: T-804 — Recording detail page and staff actions.

### 2026-11-20 01:06 — T-802 Web foundation and staff authentication — DONE
- The user chose an **Armenian** dashboard. They also asked to continue task by task without waiting for approval, until they say stop.
- `src/reel/web/`:
  - `security.py`: scrypt passwords, `SessionSigner` (HMAC-signed cookie), CSRF check, `LoginThrottle`;
  - `app.py`: `create_app` — session middleware, the `staff` dependency (anonymous → `/login?next=`), an app-wide CSRF guard, login/logout, the start page with job counts by status, Armenian error pages, security headers;
  - `texts.py`: Armenian stage and status names and messages;
  - `templates/` (base, login, home, error) and `static/style.css` (plain CSS, light and dark);
  - `commands.py`: `reel serve` and `reel user add|password|disable|enable|list`; the session secret comes from `REEL_WEB_SESSION_SECRET`, or is made once and kept in the secret store.
- Dependencies: fastapi, jinja2, python-multipart, uvicorn; httpx for tests (all free).
- README and `.env.example` updated. Rules in D-049.
- Tests: `tests/unit/test_web_auth.py` (21, FastAPI TestClient):
  - anonymous visitors → login page (with `next`); a 404 page in Armenian;
  - a wrong password and an unknown user get the same answer (401);
  - login → dashboard, a new session, `HttpOnly`/`SameSite` cookie, escaped names; logout;
  - CSRF missing, empty, forged or from another session → 403, also for logout; the header token works;
  - the session expires after `session_hours`;
  - a forged cookie, or one signed with another secret, is ignored;
  - disabling the account or changing its password ends the session;
  - 5 failures → throttled (429), free again after 15 minutes;
  - only local `next` pages; security headers; the password hash format and rules;
  - `reel user` commands; the session secret from `.env` or the store; `reel serve` uses the configured address and makes the secret;
  - a real uvicorn server answers over HTTP.
- Full suite 1089 passed with `-W error`; ruff clean.
- Next: T-803 — Recordings dashboard (started at once, as the user asked).

### 2026-11-20 00:53 — T-706 Uploaded and Posting-links stages — DONE (Phase 7 complete)
- `src/reel/stages/publishing.py`:
  - `UploadStage`: the cleaned file's SHA-256, the YouTube publisher with every state snapshot saved, progress, `ctx.check`, flags.
  - `PostStage`: Slack, Classroom and Library, each on its own row; skipped when the group has no place; unchanged targets are not sent again; previews while YouTube is in dry-run.
  - Shared: `load_lesson`, `Record`, lease-checked writes (`_owned`), `seconds_until_quota_reset`.
- Both stages are registered in `default_registry`, so every pipeline stage now exists.
- `StageFailed(retry_after=…)`, worker → `JobQueue.fail(retry_after=…)`; the wait is at most 2 days.
- `notify/staff.py`: after the upload, only `youtube_not_unlisted` (R-5) is notified.
- Rules in D-048.
- Tests: `tests/unit/test_publishing_stages.py` (13), with the fake YouTube and Classroom services, a fake Slack API and the local Library:
  - all in dry-run → Done with previews and no catalogue entry;
  - live → Done, then a re-run after a title change updates the video, the message, the post and the entry in place (one of each); the same input again sends nothing; YouTube back in dry-run keeps the real record;
  - a failing Slack post is retried alone while Classroom and Library are not sent again;
  - Slack out of retries → job Failed with Classroom and Library published and one staff alert;
  - a lost Slack answer is found again, not posted twice;
  - an interrupted upload resumes the same session (one session, one video);
  - a used-up quota waits until the reset, and the reset time is right across DST;
  - revoked Google access fails at once with "reel auth google";
  - a group without a channel or course → skipped;
  - a private lock is flagged;
  - an attempt that lost the job cannot write.
- Also: `test_notify` (+2, the R-5 alert); `test_worker` (every stage exists now).
- Full suite 1068 passed with `-W error`; ruff clean.
- Next: T-802 — Web foundation and staff authentication. Before it starts, ask the user: dashboard UI language (Armenian or English). **Waiting for the user's approval.**

### 2026-11-20 00:29 — T-701 Secrets store and Google OAuth — DONE
- `src/reel/auth/store.py` (`WindowsCredentialStore` via ctypes/advapi32, `FileSecretStore` for development, `MemorySecretStore`, `make_secret_store`), `src/reel/auth/google.py` (`OAuthClient.from_file`, `authorize` — loopback + PKCE + state, `GoogleTokens` — refresh, cache, invalidate, rotation, `AuthError`, `connection`, `forget`, `make_google_tokens`), `src/reel/auth/commands.py` (`reel auth google|status|forget`). Config `[auth]` (`store`, `store_namespace`, `dev_file`). `GoogleApiClient` passes `AuthError` through. `docs/setup/google.md`; `.gitignore` covers `config/google-client*.json`; README and `.env.example` updated. Design in D-047.
- Tests: `tests/unit/test_google_auth.py` (15, fake Google token server and a fake browser: sign-in stores the token and logs no secret; denied / wrong state / timeout store nothing; refresh before expiry, cache, invalidate; revoked → clear non-retryable error; outage retryable; not connected / another client; rotated token kept; publishers see the auth error itself; Desktop-client file check; forget; file store warns; **real Windows Credential Manager round trip** under a test namespace; `reel auth status`). Full suite 1053 passed with `-W error`; ruff clean.
- A real sign-in needs the user's free Google Cloud project (T-1003).
- Next: T-706 — Uploaded and Posting-links stages. **Waiting for the user's approval.**

### 2026-11-20 00:21 — T-602 Consent stage, holds and audit log — DONE (Phase 6 complete)
- `src/reel/stages/consent.py`: `ConsentStage` (provider from config by default; a `consent_checks` row per check; passed → upload; opted out / unknown → hold; unreachable → retry, hold on the last attempt; identification safety net). `JobQueue.release(advance=True)` on a consent hold writes `released_by`, `released_at`, `release_note` on the latest check row in the same transaction. Registered in `default_registry`. Rules in D-046.
- Tests: `tests/unit/test_consent_stage.py` (7: passed → upload with a complete audit row; opted out and unknown → hold, the worker leaves it, nothing uploaded, staff accept → recorded and uploaded; cancel → nothing published; unreachable → retried, held, "check again" → passes; Core Engine group id and lesson date are asked; the file provider from the config). `test_worker` updated (next missing stages: Uploaded, Posting links). Full suite 1038 passed with `-W error`; ruff clean.
- Next: T-701 — Secrets store and Google OAuth. **Waiting for the user's approval.**

### 2026-11-20 00:13 — T-504 Chapters stage — DONE (Phase 5 complete)
- `src/reel/stages/chapters.py`: `ChaptersStage` (heavy): identification gate → hold `identification`; chapters + summary (T-501), title + description (T-502), thumbnail (T-503) with fallbacks and the flag `thumbnail_not_made`; `video_metadata` row + `metadata.json`; `main_topic`. Registered in `default_registry`. `OnDemandLLM` moved to `reel.llm` (shared by `clean` and `chapters`). Rules in D-045.
- Tests: `tests/integration/test_chapters_stage.py` (3: a row with title, description with the chapter list and footer, three chapters with exact starts, a 1280×720 thumbnail; missing identification → hold, then staff set group/lesson, release → completes; unknown group → hold). `test_worker` updated (next missing stage: Consent). Full suite 1031 passed with `-W error`; ruff clean.
- Next: T-602 — Consent stage. **Waiting for the user's approval.**

### 2026-11-20 00:08 — T-502 Title and description (deterministic part) — DONE (GPT, reviewed by Claude)
- GPT created only its files: `src/reel/metadata/__init__.py`, `src/reel/metadata/text.py` (`Chapter`, `ChapterError`, `check_chapters`, `format_timestamp`, `render_title`, `build_description` → `DescriptionResult` with flags `description_chapters_dropped` / `_summary_truncated` / `_footer_truncated`), `tests/unit/test_metadata_text.py` (78).
- Review: tests pass here; spot check on realistic Armenian input (title with `{topic}`, description with three chapters of a 100-minute video → `0:00:00` format, `<` `>` replaced, first chapter moved to 0). Applied its requested changes: `topic` added to `TITLE_FIELDS` (used only through `{topic}`; comment in the configs), and ruff now excludes `samples`, `data`, `logs` (`pyproject.toml`).
- The user asked not to give GPT a new task until they say so.

### 2026-11-20 00:03 — T-501 Chapter generation — DONE
- `src/reel/metadata/chapters.py`: `generate_chapters` (windows → checked LLM labels → grouping without LLM → starts at transition phrases → checked LLM titles → `check_chapters` from GPT's T-502; fallback flag `chapters_not_generated`), `make_windows`, `label_window`, `group_windows`, `title_group`, `support`, `topic_summary`. Prompts `config/prompts/chapter_window.txt`, `chapter_title.txt`. Config `metadata.chapter_start_phrases` (schema, example, local). Design in D-044.
- Real lesson (first 30 min, gemma3:4b): first try showed invented labels and a bad merge; after the checks: 3 chapters, the third exactly at 19:17 «այսօր մենք անցնելու ենք …» («Սեփական ֆունկցիաներ…»); 150–200 s per 30 min.
- Tests: `tests/unit/test_chapters.py` (10: valid output with exact starts, windows opened by a phrase, invented label not used, invented title replaced, too few topics → flag, chapters outside the video → flag, one unreadable answer survived, most failing → error for retry, support with letter folding, topic summary). Full suite 1028 passed with `-W error` (includes GPT's T-502 work in progress); ruff clean.
- Next: T-504 needs GPT's T-502 (report not written yet) and T-503 (done). **Waiting for the user's approval.**

### 2026-11-19 23:39 — T-406 Cleaned stage and transcript remap — DONE (Phase 4 complete)
- `src/reel/stages/cleaning.py`: `CleanStage` (heavy): boundaries → empty board → silences → cut list → render → clean transcript; one transaction for cut list + clean transcript + flags; `cleaning.json` summary; `OnDemandLLM` (Ollama started only when needed, stopped before rendering); `source_files` (multi-part lessons from `media_info["parts"]`). Registered in `default_registry`. Rules in D-043.
- `src/reel/cleaning/remap.py`: `remap_transcript`, `remap_segment` (words by their middle; segments kept when ≥ half is kept; times through the cut list).
- Tests: `tests/unit/test_remap.py` (7), `tests/integration/test_clean_stage.py` (4: one file and two parts — the empty silent part and the chatter before/after are cut, the video length equals the cut list, the clean transcript is ordered and inside the video, the cut-out line is dropped, "cut to" recorded; a missing raw transcript fails at once; no start/end → flags, nothing cut at the edges). `test_worker` and `test_whisper` updated for the new stage. Full suite 940 passed with `-W error`; ruff clean.
- Speed note: on the real 4K lesson the silence analysis (T-401) is the slowest analysis (≈0.2 × real time, it decodes every frame on the CPU); a later optimisation can decode on the GPU like the board check.
- Next: T-501 — Chapter generation. **Waiting for the user's approval.** GPT is doing T-502.

### 2026-11-19 23:20 — T-704 Google Classroom publisher — DONE (GPT, reviewed by Claude)
- GPT created only its files: `src/reel/publish/classroom.py` (`ClassroomPublisher`, `ClassroomPost`, `ClassroomResult`, `render_text`; announcement or course-work material with a link card, `PUBLISHED`; recovery by link or by a one-line footer `Reel · <16 hex>` derived from the job (visible in the post); a changed video link → new post first, then the old one deleted (Classroom cannot patch attachments); teacher-deleted post → recreated; dry-run without HTTP), `tests/unit/test_classroom.py` (67), `tests/unit/classroom_fake.py`.
- Review: 67 tests pass here; ruff clean; delete paths read — only posts with this job's marker or posts stored for this job are deleted, never one matched only by its link. Note for T-706: pass the database `instance_id` (as for Slack).
- Next for GPT: `handoff/T-502.md`. Claude starts T-406 (approved by the user).

### 2026-11-19 23:15 — T-405 Renderer and loudness normalisation — DONE
- `src/reel/media/render.py`: `render` (two-pass loudness, GPU first with CPU fallback, atomic `.part`), `build_graph` (trim/atrim per kept segment, 30 ms fades, concat, graph in a file), `output_size` (shorter side ≤ `max_height`, never up), `probe_source` (rotation, audio). `run_ffmpeg` got an `on_stderr` callback (to read measurements). Config: `render.video_encoder = "auto"` (new default), `render.qsv_quality = 26`. Rules and measurements in D-042.
- Real lesson, both parts (100.3 min 4K HEVC → 1080p): 969 s with Quick Sync (0.16 × real time); video length = cut list ± 1 frame; −28.9 → −16.17 LUFS, true peak −1.5. Quality check: at Quick Sync quality 26 the chalk writing is clearly readable and the bitrate halves (≈1 GB per 100 min). Temporary benchmark files deleted; `samples/bench/cleaned.mp4` (private) kept for the next tasks.
- Tests: `tests/integration/test_render.py` (11: exact duration, audio = video ± 1 frame, never scaled up, scaled down keeping the shape, loudness within ±1 LU, no clicks at joins incl. the file join, Quick Sync or fallback, silent audio, cancellation, graph). Full suite 929 passed with `-W error` (includes GPT's T-704 work); ruff clean.
- Next: T-406 — Cleaned stage and transcript remap. **Waiting for the user's approval.** GPT's `handoff/T-704.report.md` is present (to review when the user says).

### 2026-11-19 22:48 — T-404 Cut list and timeline mapping — DONE
- `src/reel/cleaning/cutlist.py`: `Part`, `Timeline` (parts as one raw timeline: `locate`, `to_raw`, `snap`), `Cut`, `boundary_cuts`, `build_cut_list` (clip, padding only at lesson boundaries, merge, min remove/keep, frame alignment, split at part edges), `CutList` (`to_clean`, `to_raw`, `keeps`, `final_duration`, JSON schema 1), `write_cut_list`/`read_cut_list` (atomic), `save_cut_list` (new DB version per run)/`load_cut_list`. Rules in D-041.
- Real lesson: parts 2860.393 s + 3159.922 s; with the T-402 end the list keeps 6018.823 of 6020.315 s; the join is seamless.
- Tests: `tests/unit/test_cutlist.py` (14: merging, padding at 0 and at the end, already-shaped removals not padded, minimums, frame alignment, parts, monotonic mapping, clean→raw→clean identity, final duration, nothing kept, JSON and DB versions). Full suite 851 passed with `-W error`; ruff clean for Claude's files (2 long lines in GPT's unfinished `classroom.py`, T-704 in progress).
- Next: T-405 — Renderer and loudness normalisation. **Waiting for the user's approval.**

### 2026-11-19 21:00 — T-503-fix and T-702 YouTube publisher — DONE (GPT, reviewed by Claude)
- T-503-fix: title wrapped by measured glyph widths (`measure_text`, `wrap_measured`). Real 4K lesson: the full title «Սեփական ֆունկցիաներ, callable օբյեկտներ և hash աղյուսակներ Python-ում» now fits in 2 lines without an ellipsis (checked by eye).
- T-702: `src/reel/publish/google_api.py` (`AccessTokens` protocol for T-701, `GoogleApiClient` with one refresh after 401, error classification, no redirects, tokens registered for redaction), `src/reel/publish/youtube.py` (`YoutubePublisher`: resumable upload with saved state via `on_state`, upload-session URL kept DPAPI-protected in details, marker + file tags to find an earlier upload, `PRIVACY = "unlisted"` in every body, same-file re-run updates, new-file re-run replaces — old video deleted only with `delete_replaced_videos`, playlist idempotent, R-5 flag `youtube_not_unlisted`, thumbnail 403 flag, dry-run without HTTP), `tests/unit/test_youtube.py` (60) + `tests/unit/youtube_fake.py`.
- Review: only its files changed; 110 of its tests pass here; privacy and deletion paths read; full suite 837 passed with `-W error`; ruff clean. Notes for T-706: persist every `on_state` snapshot, one attempt per job at a time, pass `ctx.check` and progress.
- Next for GPT: `handoff/T-704.md` (Classroom publisher).

### 2026-11-19 20:48 — T-403 Break detection (empty board) — DONE
- User's rule (D-040, replaces D-013): nobody at the board for more than 10 s → shortened to 5 s, also when someone is heard (asked; the user chose "always").
- `src/reel/cleaning/presence.py`: `analyze_board` (FFmpeg pictures → YOLOX person detector → `empty_stretches`), `PersonDetector` (onnxruntime, CPU), `BoardAnalysis`, `ModelMissing`, pinned `MODELS`. Keyframes only when dense enough, GPU decoding (d3d11va) with CPU fallback, picture times from `showinfo`, cancellation, progress.
- Config: `[cleaning.breaks]` replaced by `[cleaning.empty_board]` (`min_empty_seconds` 10, `keep_seconds` 5, `model`, `person_score` 0.4, `min_person_height` 0.08, `sample_seconds` 1, `hwaccel` auto) in the schema, example and local config. `scripts/setup_person_model.py` installed YOLOX-tiny (20 MB, Apache-2.0) into `models/person`.
- Real lesson: both parts checked (137 s + 157 s); the lecturer is at the board in 7073 of 7077 pictures; the only empty moment (<2 s, checked by eye) is kept → no cut. Detection boxes checked: one moving figure, ≈35 % of the picture height.
- Tests: `tests/unit/test_presence_logic.py` (9), `tests/integration/test_presence.py` (6: keyframe/fps/GPU modes, exact 5 s kept, cancellation, missing model, real model on an empty picture). Full suite 834 passed with `-W error` (includes GPT's work in progress); ruff clean.
- Next: T-404 — Cut list and timeline mapping. **Waiting for the user's approval.**

### 2026-11-19 20:22 — T-402 Lesson start/end detection — DONE
- Finished after the pause: the LLM end judge on the real last 30 minutes (forced with `fuzzy_threshold=0.99`): gemma3:4b named a wrong line with garbled words → rejected by the quote check → no cut (75 s). Phrase matching remains the main method (D-039).
- `youtube.delete_replaced_videos = false` added to the schema, example and local config (needed by T-702).
- Full suite 756 passed with `-W error` (includes GPT's T-503 tests); ruff check and format clean. D-039 written; D-031…D-039 added to the decision table; ARCHITECTURE status updated.

### 2026-11-19 20:21 — T-503 Thumbnail — DONE (GPT, reviewed by Claude)
- GPT created only its files: `src/reel/media/thumbnail.py` (`make_thumbnail`, `ThumbnailResult`, `MissingGlyphs`, cmap formats 4/12 with `struct`, literal text via `textfile`, atomic JPEG under 2 MB, cancellation), `tests/unit/test_thumbnail_logic.py` (34), `tests/integration/test_thumbnail.py` (10).
- Review: 44 tests pass here; ruff clean. Real 4K lesson: 1280×720 JPEG, 115 KB, 3.4 s, Armenian text drawn without boxes. One issue: lines are wrapped by the widest glyph, so titles use half the width and get an ellipsis too early → small fix `handoff/T-503-fix.md` given to GPT, then `handoff/T-702.md`.

### 2026-11-19 19:18 — T-402 work before the pause
- `src/reel/cleaning/boundaries.py`: Armenian `normalize` + `phonetic` folding (օ/ո, է/ե, ֆ/վ, ռ/ր, բ/փ/պ, գ/ք/կ, դ/թ/տ, ձ/ց/ծ, ջ/չ/ճ, եւ/եվ), fuzzy `find_phrases`; `find_start` = greeting within `anchor_window_minutes` before a topic opener, else LLM; a topic opener alone is NOT enough; `find_end` = last closing phrase (segment end), else LLM; `detect_boundaries` (conflict → no cut + `lesson_boundaries_conflict`); flags `lesson_start_not_found` / `lesson_end_not_found`; LLM errors propagate (stage retries).
- LLM guard: the answer must give `quote` (the line's own words); Reel verifies it is in that line (`QUOTE_THRESHOLD` 0.8), else rejected. LLM text limit 8000 chars: the start keeps the window's first lines, the end keeps the **last** lines (`keep_last=True`; fixed 19:17 — the real last 30 min exceed 8000 chars and the farewell is the last line).
- Prompts `config/prompts/lesson_start.txt`, `lesson_end.txt` (Armenian; recap/teaching is not a start; answer found:false if unsure).
- `tests/unit/test_boundaries.py`: 24 passed.
- Real lesson (private, `samples/bench/`): `start30.json` (first 30 min of IMG_0359) — starts with a 19-min recap, «այսօր մենք անցնելու ենք…» at 19:17; result: start not found → nothing cut (correct; gemma3:4b invented «ողջույն», quote check rejected it). `end30.json` (last 30 min of IMG_0360, offset 1359.9 s) — closing «…այսօր վամար այսքանը շնորակալություն կա անդիպենք աջոր դաս» found by phrase match at 1798.0 s of 1800 s (correct, no LLM needed).

Resume steps: done (see the entry above).

### 2026-11-19 18:34 — T-801 Staff notifications — DONE (GPT, reviewed by Claude)
- GPT created only its allowed files: `src/reel/notify/staff.py` (`StaffNotifier`: outbox over `job_events` with a watermark in `app_meta`, first run skips history, retryable error → retried next run, permanent → skipped and logged, dry-run logs only, recovery of a sent-but-unacknowledged alert through Slack metadata), `tests/unit/test_notify.py` (42).
- Review: 42 tests pass here; ruff clean. Applied its requested change: `stage_completed` events now carry a `flags` snapshot. Its two other notes were added to T-901 (one notifier per database) and T-902 (event ids must never be reused).

### 2026-11-19 18:13 — T-304 Local LLM (Ollama) client — DONE
- Ollama v0.40.16 installed inside the project (user's choice) by `scripts/setup_ollama.py` (SHA-256 checked); models pulled into `models/ollama`; new config `paths.ollama`, `llm.use_igpu`.
- `src/reel/llm/`: `LLMClient`, `LLMResult` (tokens, seconds, attempts), `LLMError`, `FakeLLM`, `OllamaClient` (streaming, JSON-schema output, validation, retries), `OllamaServer` (starts the project's Ollama when needed), `make_llm`.
- Armenian test (`scripts/benchmark_llm.py`, real lesson transcript): gemma3:4b chosen over qwen3:8b (3× faster, fewer tokens, correct topic); the integrated GPU makes it about 45 % faster (D-038). qwen3:8b deleted (5 GB).
- Tests: `tests/unit/test_llm.py` (16, mocked HTTP: success, invalid JSON → retry, schema mismatch, timeout → retry → error, 5xx, missing model, no server, cancel, server start environment) and `tests/integration/test_ollama.py` (live: the project's Ollama returns schema-valid Armenian JSON, 16 s). Full suite 646 passed with `-W error`; ruff clean except GPT's unfinished T-801 file.
- Next: T-402 — Lesson start/end detection. **Waiting for the user's approval.**

### 2026-11-19 18:03 — T-705 Academy Library port — DONE (GPT, reviewed by Claude)
- GPT created only its allowed files: `src/reel/library/` (port, local upsert into `LibraryEntry`, HTTP client `PUT /entries/{key}`, mock server), `docs/integrations/academy-library.md`, `tests/unit/test_library.py` (71).
- Review: local upsert is atomic (`INSERT ... ON CONFLICT DO NOTHING` then update, inside the caller's transaction or its own `BEGIN IMMEDIATE`); the HTTP key `reel-<instance id>-job-<id>` is stable across retries and unique per installation; responses strictly validated; token never exposed. 71 tests pass here. Accepted; its two suggestions applied (ARCHITECTURE section 8 key name; the LLM test mock ignores client disconnects).

### 2026-11-19 17:47 — User requirement: output at most 1080p
- Videos above 1080p (4K) are scaled down to 1080p, smaller ones never up (D-037, `render.max_height`, T-405).

### 2026-11-19 17:40 — T-703 Slack publisher — DONE (GPT, fix reviewed)
- Fix checked: read methods form-encoded, write methods JSON; channel IDs validated before any HTTP call. Only GPT's two files changed; 95 Slack tests pass here; full suite 572 passed, 1 skipped (live Ollama test until the model is pulled).

### 2026-11-19 17:16 — T-303 STT benchmark and default model — DONE
- Material: the user's real lesson (2 iPhone files, 4K HEVC 24 fps, AAC; 47.7 + 52.7 min; private, only in `samples/`). Excerpt: minutes 15–25 of part 1 (Python lesson: built-in functions, list and string methods). Tools: `scripts/benchmark_stt.py`; all models downloaded by `scripts/setup_whisper.py`.
- PC: Intel i5-1335U (10 cores / 12 threads, laptop), 16 GB RAM. "CER vs large-v3" = character difference to large-v3's text on the same 2 minutes (agreement, no human transcript exists).

| Model | Settings | Audio | Time | × real time | Result |
|---|---|---|---|---|---|
| small | beam 5, fallback, 4 threads | 10 min | 1379 s | 2.30 | mostly wrong words |
| medium | beam 1, no fallback | 2 min | 58 s | 0.49 | repetition loops; CER 0.56 |
| large-v3 | beam 1, no fallback | 2 min | 107 s | 0.89 | best text (reference) |
| large-v3-turbo | beam 5, no fallback | 2 min | 47 s | 0.39 | CER 0.40 (skips more) |
| **large-v3-turbo** | **beam 1, no fallback** | 2 min | 37 s | **0.31** | **CER 0.29** |
| large-v3-turbo in Reel | + VAD, word timestamps, silence filter | 10 min | 511 s | 0.85 | 628 words |
| **large-v3-turbo in Reel** | **+ VAD, no word timestamps** | 10 min | 329 s | **0.55** | 570 words, 98.6 % Armenian letters |

- Other measurements on the real 4K file: import of 47.7 min = SHA-256 15 s + validation 6 s + audio 29 s; silence analysis (T-401) 0.20 × real time; render to 1080p H.264 (`veryfast`, CRF 21) 0.42 × real time.
- **Estimate for a 2 h 41 min lesson on this PC:** import ~3 min + transcription ~89 min + silence analysis ~33 min + LLM steps (T-304, not measured yet) + render ~60 min (about 140 min after cleaning) ≈ **3 h + LLM + upload**. A lesson ending at 21:00 would be ready after midnight → risk to M-2 (D-036); hardware encoding/decoding to be tried in T-405, real run in T-1002.
- Defaults set (schema, example and local config): `large-v3-turbo`, beam 1, no fallback, no word timestamps, automatic threads (D-036). New option `stt.temperature_fallback`; Reel drops repetition-loop windows (compression ratio > 2.4). Tests: `tests/unit/test_whisper_options.py` (4). Full suite 540 passed with `-W error` (includes GPT's T-703 tests); ruff clean.
- Found: the sample lesson is two files → D-035 (multi-part upload, T-807).
- Models: small, medium and large-v3 deleted after the benchmark (user, 5 GB); kept large-v3-turbo and tiny (tests).
- Next: T-304 — Local LLM (Ollama) client. **Waiting for the user's approval.**

### 2026-11-19 17:12 — T-703 Slack publisher — reviewed (GPT), fix requested
- GPT created only its allowed files (`src/reel/publish/slack.py`, `tests/unit/test_slack.py`, 76 tests). Review: idempotency (saved id → update; else metadata search in history → update; else post), dry-run makes no calls, retryable vs permanent errors, token never logged — correct; 76 tests pass here.
- Two changes requested: `chat.getPermalink` / `conversations.history` must be form-encoded (GPT spotted that the T-703 prompt's "JSON for all methods" was wrong), and channel IDs must be validated before calling Slack (a channel name would fail only after posting).

_Times of the 2026-11-19 entries from T-202 on were corrected to the real clock (file timestamps) at 16:45; they had been written too late._

### 2026-11-19 16:45 — T-601 Consent provider — DONE (GPT, reviewed by Claude)
- GPT created only its allowed files: `src/reel/consent/` (`ConsentProvider`, `ConsentDecision`, `make_consent_provider`, file and HTTP providers, loopback mock server), `config/consent.example.json`, `docs/integrations/core-engine-consent.md`, `tests/unit/test_consent.py` (85).
- Review: code read; rules match D-019 (only an all-granted, non-empty roster passes; unknown status → opted out; unknown group → unknown; timeout/5xx/auth/malformed → unreachable, never a pass); only student id + status kept; token never logged; redirects and proxies disabled. 85 tests re-run here and passed; ruff clean. Accepted as is.
- `ruff format` now skips `handoff/` (it had reformatted a code block in a report).

### 2026-11-19 16:20 — New requirement and real recordings
- User: the website must let staff upload videos → new task **T-807** (after T-802).
- User: the two sample files are parts of one lesson; several files uploaded together form one lesson, the confirm button means complete (D-035, part of T-807).
- User: real lesson videos are in `Desktop/reel_videos` (2 iPhone `.MOV`, 6.5 and 7.2 GB); copied to `samples/` (private, not in git; originals untouched). T-303 started.

### 2026-11-19 16:17 — T-401 Silence and static-video detection — DONE (GPT, reviewed by Claude)
- GPT created only its allowed files: `src/reel/cleaning/silence.py` (`analyze_silence`, one FFmpeg pass with `silencedetect` + `freezedetect` at 160 px / 5 fps; pure functions `parse_detection_log`, `intersect_intervals`, `removable_silences`), `tests/unit/test_silence_logic.py` (32), `tests/integration/test_silence.py` (17); report `handoff/T-401.report.md`.
- Review: code read in full; acceptance criteria covered; 49 tests re-run here and passed; full suite 375 passed; ruff clean. Accepted as is. Note for T-1002: the analysis decodes the whole video, so its time on a 3-hour 1080p file must be measured. GPT's optional suggestion (`on_stderr` in `run_ffmpeg`, to drop its private runner) is left for a later cleanup.

### 2026-11-19 16:15 — T-302 Local Whisper transcriber and Transcribed stage — DONE
- Installed faster-whisper 1.2.1 and its free dependencies in `.venv` (added to pyproject); `scripts/setup_whisper.py` downloaded `tiny` (78 MB) into `models/whisper/tiny`; nothing was written to the user profile (checked).
- `src/reel/stt/whisper.py` (`WhisperTranscriber`, `make_transcriber`) and `src/reel/stages/transcribing.py` (`TranscribeStage`, registered) — D-034.
- Progress reporting for every stage: `StageContext.report_progress()` + heartbeat → `StageRun.metrics["progress"]`.
- Found while testing: PyAV 19 breaks faster-whisper's decoder → Reel reads the WAV itself; numpy numbers converted to plain floats.
- Tests: `tests/unit/test_transcribe_stage.py` (6, fake engine: storage, prompt, progress via heartbeat, missing audio/model, empty result, repeat filter) and `tests/integration/test_whisper.py` (2, real `tiny` model: import → transcribe end to end; English speech from the Windows synthesiser is really recognised). Import tests now run only the import stage. Full suite 375 passed with `-W error`; ruff clean.
- Next: T-303 needs the real recording (large-v3, about 3 GB). **Waiting for the user's approval.**

### 2026-11-19 16:05 — T-301 Transcript model and STT interface — DONE
- `src/reel/stt/`: `Transcription` / `Segment` / `Word` with validation, dict/JSON round trip, atomic JSON files, `save_transcript` / `load_transcript` (raw and clean rows), `Transcriber` interface and `FakeTranscriber` with a scripted Armenian lesson (D-033).
- Tests: `tests/unit/test_transcript.py` (21). Full suite 367 passed with `-W error` (includes GPT's unfinished T-401 tests); ruff clean.
- Next: T-302 — Local Whisper transcriber and Transcribed stage (installs faster-whisper, MIT, and downloads the `tiny` model into `models/whisper` for the smoke test). **Waiting for the user's approval.**

### 2026-11-19 16:00 — T-204 Import stage — DONE
- `src/reel/stages/importing.py` (`ImportStage`, registered in `default_registry()`): hash and duplicate check, validation, media info, identification, `audio.wav` (D-032).
- Queue/worker: new `Discard` stage exception and `JobQueue.discard()` for jobs a stage cancels.
- A recording added without a file time (not by the watcher) gets it from the file, so the date fallback still works (found by a test).
- Tests: `tests/integration/test_import_stage.py` (8: valid file → `transcribe` with audio 16 kHz mono; copy under another name/folder → cancelled as duplicate; 3 copies at once → one job; junk and too-short files fail at once with a notification; missing file fails after retries; unknown names flagged but processing continues; re-run is safe). `test_worker` CLI test updated (import is now implemented).
- Full suite: all of Claude's tests pass with `-W error`; ruff reports one long line in GPT's unfinished T-401 test file (to be fixed in its review).
- Next: T-301 — Transcript model and STT interface. **Waiting for the user's approval.**

### 2026-11-19 15:55 — T-203 Group and lesson identification — DONE
- Q-5 asked and answered (staff rename files; a missing number waits for staff).
- `src/reel/ingest/identify.py`: `identify()` / `identify_recording()` return group, lesson number, date, flags and how each was found; `Identification.apply()` stores them on the recording and the job flags (used by T-204). Never raises for odd names.
- Config: a second default date pattern `19.11.2026` (schema, example and local reel.toml).
- Also D-031: GPT (Codex) gets T-401, T-601, T-703, T-705 through `handoff/`.
- Tests: `tests/unit/test_identify.py` (24, English and Armenian names, aliases, sub folders, ambiguous numbers, date fallbacks and time zone). One bug found by a test and fixed: a media time without a zone could not be compared. Full suite: 289 passed with `-W error`; ruff clean.
- Next: T-204 — Import stage. **Waiting for the user's approval.**

### 2026-11-19 15:40 — T-202 Folder watcher — DONE

**What was done** (brief, to save tokens)
- `src/reel/ingest/watcher.py`: `FolderWatcher.scan()` and `run(stop)`.
  - It scans every watch folder recursively, keeping the configured extensions (case-insensitive) and skipping ignored names, also for whole folders such as `.sync`.
  - In one locked transaction it records new files as `waiting`, restarts the wait when a file changes, and marks files deleted early as `gone`.
  - When a file has been unchanged for `stable_seconds` and is not empty, it creates the Recording and exactly one queued import job (`create_job`) in the same transaction.
  - Missing watch folders are reported once, and their files are left as they are.
  - Rules: D-024 "Details".
- `src/reel/ingest/commands.py`: `reel watch` (until Ctrl+C) and `reel watch --once` (one scan and a summary). `src/reel/ingest/__init__.py`. `cli.py` registers the command.

**Files created / modified**
- Created: `src/reel/ingest/__init__.py`, `src/reel/ingest/watcher.py`, `src/reel/ingest/commands.py`, `tests/unit/test_watcher.py`.
- Modified: `src/reel/cli.py`, `TASKS.md`, `PROGRESS.md`, `DECISIONS.md` (D-024 details), `ARCHITECTURE.md`.

**Tests performed and results**
- `pytest -W error`: **265 passed** (9 new). `ruff`: clean. `reel watch --once` in the project: "0 file(s) …" (the inbox is empty; no test data was put into the real database).
- What the tests cover (fake clock, temporary folders):
  - a stable file is queued once (Recording fields, `detected_at` = first sighting, link to the watcher row);
  - a growing file is not queued until it stops changing;
  - a restart (a new watcher) queues nothing again;
  - unsupported extensions, temporary, hidden and partial files and hidden folders are ignored, while upper-case extensions, Armenian names and subfolders work;
  - a file deleted early becomes gone, and gets a new chance when copied again;
  - a replaced file becomes a new job;
  - empty files wait;
  - a missing watch folder keeps its files;
  - `reel watch --once` prints the summary.
- Acceptance criteria:
  - a growing file is not enqueued — pass;
  - a stable file is enqueued once — pass;
  - a restart does not enqueue it again — pass;
  - unsupported extensions are ignored — pass;
  - a file deleted before it becomes stable is ignored — pass.

**Problems encountered and how they were resolved**
- One test expected the original letter case in the stored path. Paths are stored case-insensitively on Windows by design, so the test was corrected.

**Next task**
- T-203 — Group and lesson identification. **Waiting for the user's approval.**

### 2026-11-19 15:45 — T-201 Media toolkit and synthetic test media — DONE

**What was done** (the user asked to save tokens, so this entry is brief)
- `src/reel/media/ffmpeg.py`:
  - `run_ffmpeg` (progress in seconds and as a fraction, cancellation through `on_cancel`, errors with FFmpeg's message; returns the last progress values);
  - `probe` → `MediaInfo` (duration, size, container, video codec/size/fps, audio codec/rate/channels, creation time; `to_dict()` for `Recording.media_info`);
  - `validate` (D-030);
  - `extract_audio` (16 kHz mono WAV, written as `.part` and then renamed);
  - errors `InvalidMedia` and `FfmpegCancelled`.
- `src/reel/media/testmedia.py` (`Part`, `make_lesson`, `parse_parts`, `DEFAULT_LESSON` = 3 s noise + 4 s static silence + 3 s tone) and `scripts/make_test_media.py`.

**Files created / modified**
- Created: `src/reel/media/ffmpeg.py`, `src/reel/media/testmedia.py`, `scripts/make_test_media.py`, `tests/integration/test_media.py`.
- Modified: `TASKS.md`, `PROGRESS.md`, `DECISIONS.md` (D-030), `ARCHITECTURE.md`, `README.md`.

**Tests performed and results**
- `pytest -W error`: **256 passed** (13 new). The media tests passed 3 times in a row. `ruff check` / `ruff format --check`: clean.
- What the tests cover:
  - probe values match the generated file (10 s, h264 320×240 25 fps, aac 48 kHz mono, creation time);
  - a good file passes validation;
  - five bad files fail with a reason: garbage, truncated with the index at the end, truncated with the index first ("no decodable video … truncated"), no audio, too short;
  - extracted audio is 16 kHz mono 16-bit, 10.0 s ± 0.05 s; progress increases up to 1.0;
  - a failed extraction leaves no file;
  - a running FFmpeg is stopped within 5 s and raises `FfmpegCancelled`;
  - FFmpeg errors carry their message;
  - progress lines are parsed;
  - the parts text format and the script work.
- Acceptance criteria:
  - probe values match — pass;
  - a corrupt or truncated file fails validation with a reason — pass;
  - extracted audio is 16 kHz mono with the right duration (±0.05 s) — pass.

**Problems encountered and how they were resolved**
- Two test mistakes in the first run: a regex with brackets that needed escaping, and a minimum duration applied to every case. Both were fixed in the test; the code was right.
- `-W error` exposed a real flaw: the FFmpeg pipes were not closed (ResourceWarning). Fixed by using the process as a context manager.

**Next task**
- T-202 — Folder watcher. **Waiting for the user's approval.**

### 2026-11-19 15:25 — T-103 Worker runtime — DONE

**What was done**
- New session (2026-11-19). The user said "Continue your work"; resumed from this file. The full test suite passed before any change (220).
- The user now writes partly in English and asked to keep replying in Armenian (saved in the assistant's memory).
- `src/reel/queue/stage.py`, the stage contract:
  - `StageHandler` (`name`, `heavy`, optional `timeout_seconds`, `run(ctx)`) and `StageResult` (metrics, message);
  - outcome exceptions `Hold`, `StageFailed(retryable=...)` and `StageCancelled`;
  - `StageContext` (claim, configuration snapshot, database, job folder `data/jobs/<id>`, logger, plus `check()`, `wait()`, `on_cancel()` for cooperative stopping);
  - `StageRegistry` (registration, pipeline order, heavy stages, missing stages).
- `src/reel/queue/worker.py`: `Worker` with `workers.count` slots. Each slot:
  1. reads the configuration again (NFR-9);
  2. claims a job with the heavy-stage limit;
  3. runs the stage in its own thread with the job context for logging;
  4. while the stage runs, renews the lease every third of `lease_seconds`, enforces the timeout, notices staff cancels (`LeaseLost`) and the worker stopping;
  5. then completes, fails, holds or interrupts the job.

  A stage that does not stop within `stop_grace_seconds` is abandoned, and its late result is ignored. `Attention`/`Notifier` hook for failures and holds (default: a log warning; Slack in T-801). `--until-idle` mode.
- `src/reel/queue/commands.py`: `reel worker [--until-idle]`. It uses the JSON log `logs/reel-worker.jsonl` and the job logs. Ctrl+C, Ctrl+Break and SIGTERM stop it gracefully, and the previous signal handlers are restored. At start it reports the stages that have no implementation yet.
- `src/reel/stages/__init__.py`: `default_registry()`. It is still empty; the first stage arrives with T-204, and `reel worker` reports that.
- `JobQueue.active_count()` (queued or running jobs, optionally per stage).
- Config: `workers.idle_poll_seconds` (5 s) and `workers.stop_grace_seconds` (30 s) in the schema and the example file; `stage_timeout_minutes` got a comment.

**Files created / modified**
- Created: `src/reel/queue/stage.py`, `src/reel/queue/worker.py`, `src/reel/queue/commands.py`, `src/reel/stages/__init__.py`, `tests/unit/test_worker.py`.
- Modified: `src/reel/queue/__init__.py`, `src/reel/queue/jobs.py` (`active_count`), `src/reel/cli.py` (`worker` command), `src/reel/config/schema.py`, `config/reel.example.toml`, `TASKS.md`, `PROGRESS.md`, `DECISIONS.md` (D-029), `ARCHITECTURE.md` (§9, §11, status line), `README.md`.

**Commands executed**
- `.venv\Scripts\python -m pytest` (full suite, also with `-W error`; the worker tests 6 times in a row)
- `.venv\Scripts\ruff format .` · `.venv\Scripts\ruff check .`
- In the project: `reel worker --until-idle` → "worker started: 2 slot(s), heavy-stage limit 1, …", a warning listing the 7 stages without implementation, "worker stopped", exit 0; the same lines in `logs\reel-worker.jsonl`. `reel worker --help`.

**Tests performed and results**
- `pytest`: **243 passed** (23 new in `tests/unit/test_worker.py`), no warnings with `-W error`. The worker tests passed 6 times in a row.
- Normal work:
  - a job passes all 7 stages in order; the job folder is created; the stage sees the job context (job, stage, attempt, worker) and the configuration snapshot;
  - stage metrics and the message are saved.
- Failures and holds:
  - a failing stage is retried and then fails, and the notifier is called once with "failed";
  - a fatal failure is not retried;
  - an unexpected exception is retried (error text `ValueError: boom`), and then the job finishes;
  - `Hold` puts the job on hold, nothing after it runs, and the notifier is called with "held";
  - an invalid hold reason fails the job;
  - a failing notifier does not stop the worker.
- Heavy-stage limit: 3 jobs on 3 slots. With limit 1, at most 1 heavy stage runs at a time. With limit 3, all 3 overlap, which shows the measurement can see parallel stages.
- Stopping:
  - stopping gives the running job back (queued, no lease, attempt not counted, StageRun interrupted, stage told why);
  - a stage that ignores the stop is abandoned after the grace time (stop took < 5 s), and its late result changes nothing;
  - a stage running past its timeout fails ("Transcribed timed out after 0.2 s") and the notifier is called;
  - a staff cancel stops the running stage through the heartbeat, and the job stays cancelled;
  - heartbeats renew the lease during a long stage;
  - cancel callbacks run;
  - a worker without stage implementations claims nothing;
  - starting twice is an error;
  - Ctrl+C asks the worker to stop, and the old signal handler comes back.
- Registry: pipeline order, heavy stages, missing stages; a duplicate registration is refused.
- `reel worker`:
  - without a database it gives exit 1 and points to `reel db init`;
  - with no implementations it reports them and leaves the job queued, with the JSON log written;
  - with registered (dummy) stages two jobs run to Done, and the per-job `job.log` contains the transitions.
- `ruff check` / `ruff format --check`: clean.
- Acceptance criteria:
  - a job passes all stages in order — pass;
  - a failing stage is retried, then fails, and the notification hook is called — pass;
  - a stage that raises Hold puts the job On hold — pass;
  - with 3 jobs and limit 1, only one heavy stage runs at a time — pass;
  - stopping the worker releases its leases — pass.

**Problems encountered and how they were resolved**
- While designing, `--until-idle` turned out to need care: stopping as soon as nothing is due would leave jobs that wait for a retry. It now stops only when no queued (including waiting) or running job is left at a stage it can run.
- An edit script for the documents failed halfway: a backslash in a Windows path became a control character in the script. TASKS.md and ARCHITECTURE.md had already been updated correctly. README.md and DECISIONS.md were then edited directly, and the result was checked.
- Ruff re-formatted 2 files.

**Decisions**
- D-029 (worker runtime). Beyond the plan, and small: `--until-idle`, cancel callbacks for external processes, and the two new settings.

**Remaining issues**
- None for T-103. Real stage implementations start with T-204 (import). Slack notifications for failures and holds come in T-801. A long-running `reel worker` (without `--until-idle`) was not stopped with a real Ctrl+C from a console. It is covered by the in-process signal test and the stop tests.

**Next task**
- T-201 — Media toolkit and synthetic test media: FFmpeg wrappers `probe()`, `validate()`, `extract_audio()`, a runner with progress and cancellation (using `ctx.on_cancel`), and `scripts/make_test_media.py` for synthetic test lessons. **Waiting for the user's approval.**

### 2026-11-18 20:28 — T-102 Job state machine and persistent queue — DONE

**What was done**
- The user approved T-102 and asked for the number of remaining tasks: 48 tasks in total, 6 were done before T-102, so 42 remained including T-102 (41 after it). 3 of them need input from the user: T-303 and T-1002 (a real Armenian lesson video) and T-1003 (test accounts).
- `src/reel/queue/jobs.py` (no new dependency):
  - `JobQueue`:
    - creation and reading: `enqueue`, `get`;
    - worker side: `claim`, `heartbeat`, `complete`, `fail`, `hold`, `interrupt`;
    - maintenance: `recover_expired`;
    - staff side: `release` (run the held stage again, or `advance=True` to accept it and continue), `cancel`, `rerun`, `retry_now`.
  - `create_job(session, recording)` creates a job inside another transaction, so the watcher (T-202) can store the file, the recording and the job together.
  - Supporting types:
    - `RetryPolicy` (attempts, backoff base/factor/cap, built from `[workers]`);
    - `JobSnapshot` (plain state after every operation);
    - `Claim` (one attempt of one stage, identified by its StageRun);
    - errors `QueueError`, `JobNotFound`, `InvalidTransition` (nothing is changed) and `LeaseLost` (the worker must stop and discard its result).
  - Each operation is one `BEGIN IMMEDIATE` transaction. Every transition writes a JobEvent with its actor and is logged with the job id. Every stage attempt has a StageRun, closed by the transition that ends it.
  - `claim()` works in this order:
    1. recovers expired leases;
    2. takes the oldest due job;
    3. can be limited to certain stages;
    4. applies the heavy-stage limit inside the same transaction (for T-103).
  - `apply_settings()` picks up changed `[workers]` settings without a restart (NFR-9).
- `reel.db.enums`: `EventKind` (13 queue event kinds) and `next_stage()`.
- Config: new setting `workers.backoff_max_seconds` (default 3600 s) in the schema and `config/reel.example.toml` (see "Problems").

**Files created / modified**
- Created: `src/reel/queue/__init__.py`, `src/reel/queue/jobs.py`, `tests/unit/test_queue.py`.
- Modified: `src/reel/db/enums.py`, `src/reel/db/__init__.py`, `src/reel/config/schema.py`, `config/reel.example.toml`, `TASKS.md`, `PROGRESS.md`, `DECISIONS.md` (D-028), `ARCHITECTURE.md` (§4 built, §11, status line).

**Commands executed**
- `.venv\Scripts\python -m pytest` (full suite with `-W error` and `--durations`; the queue and engine tests 5 times in a row)
- `.venv\Scripts\ruff format .` · `.venv\Scripts\ruff check .`
- `reel config check` in the project: still 0 errors, 1 warning (consent registry, expected until T-601).

**Tests performed and results**
- `pytest`: **220 passed** (53 new in `tests/unit/test_queue.py`), no warnings with `-W error`. The queue and engine tests passed 5 times in a row (66/66 each time).
- Normal path:
  - enqueue (event, system actor; a second job for the same recording and an unknown recording are refused);
  - `create_job` joins the caller's transaction and disappears with its rollback;
  - claim takes the oldest due job (lease time, StageRun, event);
  - all 7 stages run in order to Done (StageRuns, exact event sequence, metrics, duration, `finished_at`);
  - heartbeat extends the lease.
- Failures:
  - backoff delays 60/240/960/3600/3600 s, with the cap and without overflow;
  - policy and lease come from the settings and change with `apply_settings`;
  - retry → not due before 60 s → attempt 2 → retry after 240 s → attempt 3 → Failed (exact StageRuns and events);
  - a fatal error fails at once;
  - long errors are cut to 4000 characters;
  - `retry_now`.
- Holds:
  - hold and release run the held stage again (reason, message, staff actor, event data);
  - release with `advance` continues with the next stage, or finishes the job at the last stage;
  - the hold reason must be a code.
- Cancel and re-run:
  - cancel from queued, on hold, failed and running; for a running job the worker's heartbeat and complete get `LeaseLost`, and the job stays cancelled;
  - re-run from the failed stage repeats no earlier stage (UJ-4);
  - re-run of a Done job from cleaning (UJ-5);
  - re-run cannot skip stages;
  - re-run of a cancelled job.
- Crashes:
  - an expired lease is recovered: it counts as an attempt, the backoff counts from the expiry, and the crashed worker's late result is refused; the job later completes (NFR-2);
  - `claim` recovers expired leases by itself;
  - a restart an hour later causes no extra wait;
  - expiry on the last attempt fails the job;
  - an old claim of the same worker name cannot touch the newer attempt (all 5 worker calls);
  - `interrupt` gives the job back without using an attempt.
- Invalid operations:
  - 13 invalid staff actions (release, cancel, rerun, retry now in states where they are not allowed) are refused, and the job (including its row version) and its events stay the same;
  - an unknown job gives `JobNotFound`;
  - worker and staff names are required;
  - the clock must give time-zone-aware times.
- Limits and concurrency:
  - with heavy limit 1, a second heavy job waits while a light job runs;
  - a claim can be limited to some stages;
  - 6 threads × 12 jobs: 84 claims, each (job, stage) exactly once, all Done;
  - 3 separate processes started together × 8 jobs: 56 claims, each exactly once, all Done.
- History and logs:
  - a 12-event history covering every kind of transition, with the exact StageRuns and actors;
  - transitions are logged with the job id and event kind;
  - a new `JobQueue` on the same database (a restart) continues where the old one stopped.
- `ruff check` / `ruff format --check`: clean.
- Acceptance criteria:
  - unit tests for every transition — pass;
  - invalid transitions are rejected — pass;
  - two concurrent claimers never get the same job — pass (threads and processes);
  - a simulated crash re-queues the job, which later completes — pass;
  - backoff times are correct — pass;
  - history is preserved — pass.

**Problems encountered and how they were resolved**
- First run: 1 of 52 tests failed, and it showed a real flaw. After a crash, the retry delay was counted from the moment the recovery ran, not from when the lease expired. If the only worker crashed and was restarted an hour later, the job would still have waited another 60 s for nothing. Fixed in the code: the delay and the end time of the interrupted StageRun now count from the lease expiry. The test's expected time was corrected, and a test for the "restarted an hour later" case was added (53 tests).
- A gap in the T-003 settings: the retry delay had no upper limit, so a large `max_attempts` (allowed up to 20) would give delays of years and overflow the date range. Fixed with the new `workers.backoff_max_seconds` (default 1 hour).
- Ruff re-formatted 1 file.

**Decisions**
- D-028 (job queue rules).
- Interpretation of the task text "every transition writes a JobEvent and a StageRun": every transition writes a JobEvent, and every transition that starts or ends a stage attempt writes or closes its StageRun. Staff actions that run no stage (release, re-run, retry now, cancelling a queued job) write a JobEvent only, because no stage attempt exists for them.
- Beyond the plan, and small: `interrupt` (a graceful stop, needed by T-103), `retry_now` (a staff action planned for T-804), the heavy-stage limit and the stage filter in `claim`.

**Remaining issues**
- None for T-102. The worker loop, heartbeat timing, per-stage timeout, stage registry and graceful shutdown come in T-103. Notifications on Failed and On hold use the returned `JobSnapshot` (T-103/T-801).

**Next task**
- T-103 — Worker runtime. **Waiting for the user's approval.**

### 2026-11-18 20:12 — T-101 Database layer and models — DONE

**What was done**
- Re-read the PRD §8.2 entity list to match the tables to it. The PDF could not be rendered by the file reader (no `pdftoppm`), so its text was extracted with a small standard-library script in the session scratchpad. Nothing was added to the project for this.
- New runtime dependency: SQLAlchemy 2.1.9 (MIT, free; its only dependency, typing-extensions, was already installed). The plan said 2.0; 2.1 is the current stable series of the same 2.x API (D-027).
- Package `src/reel/db/`:
  - `base.py`: declarative base with fixed constraint names; `UtcDateTime` (aware datetimes only, stored as UTC); JSON storage (UTF-8, compact, NaN rejected); enum storage by value.
  - `enums.py`: `Stage` in pipeline order with dashboard names (D-012, FR-5), `JobStatus`, `StageRunStatus`, `ActorKind`, `TranscriptKind`, `CutAction`, `ConsentResult`, `TargetKind`, `PublicationStatus`, `SeenFileState`.
  - `models.py`: 14 tables. The 7 PRD entities: Recording, Job (+ StageRun), CutList, Transcript, VideoMetadata, ConsentCheck, Publication (as `publication_targets`, one row per target). Also JobEvent (audit), LibraryEntry (interim Academy Library), SeenFile (watcher), StaffUser (dashboard), SchemaVersion and AppMeta. Rules enforced in the database: unique content hash, one job per recording, unique rows per job (transcript kind, cut list version, publication target, metadata, library entry), unique watcher path and username, foreign keys with `ON DELETE CASCADE`/`SET NULL`, CHECK constraints for non-negative numbers, AUTOINCREMENT ids. Rules enforced in Python: SHA-256 format (lower-cased), cut-list segment shape, canonical usernames, a row version on jobs. Helpers: `Job.latest_cut_list`, `transcript()`, `publication()`, `publication_summary()` (the PRD Publication fields), `add_flag()`.
  - `engine.py`: `Database` with WAL, `foreign_keys`, `synchronous = FULL`, 30 s busy timeout; opening never creates a missing file; `transaction()` and `transaction(immediate=True)` (`BEGIN IMMEDIATE`), `session()`, `begin()`, `pragma()`, `dispose()`.
  - `versioning.py`: schema version 1; `init_database` (current schema, version row, random `instance_id`, repeat-safe); `inspect_database` (read-only; states missing/empty/current/outdated/newer/foreign/unreadable with advice); `open_database` (only a current schema); `upgrade_database` (backup, then migrations one version per transaction, then new tables); `backup_database` (SQLite online backup).
  - `commands.py`: `reel db init`, `reel db status` (state, version, instance id, journal mode, size, row counts; exit 1 unless current), `reel db upgrade`.
- Config: new setting `paths.database_file` (empty = `<data_dir>/reel.db`) in the schema, the loader (`ResolvedPaths.database_file`) and `config/reel.example.toml`. `reel config check` now reports the database: OK, a warning when it does not exist yet, an error when it is unreadable, foreign, older or newer. `reel config init` now ends with a hint to run `reel db init`.
- Ran `reel db init` in the project: created `data/reel.db` (schema version 1, instance id `c37291233cd741d1b2c01960b1f06f80`).

**Files created / modified**
- Created: `src/reel/db/{__init__,base,enums,models,engine,versioning,commands}.py`, `tests/unit/test_db_models.py`, `tests/unit/test_db_engine.py`, `tests/unit/test_db_versioning.py`, `tests/unit/test_db_cli.py`; local (not in git): `data/reel.db` (+ SQLite's `reel.db-wal`, `reel.db-shm`).
- Modified: `pyproject.toml` (SQLAlchemy), `src/reel/cli.py` (`db` commands), `src/reel/config/schema.py`, `src/reel/config/loader.py`, `src/reel/config/check.py`, `src/reel/config/commands.py`, `config/reel.example.toml`, `tests/conftest.py` (`db` fixture), `tests/unit/test_config_cli.py` (2 warnings before `db init`), `TASKS.md`, `PROGRESS.md`, `DECISIONS.md` (D-027), `ARCHITECTURE.md` (§5 built, §6, §7 table, §11, status line), `README.md`.

**Commands executed**
- `.venv\Scripts\python -m pip install -e ".[dev]"` (installed SQLAlchemy 2.1.9); `pip index versions sqlalchemy` and a wheel download into the scratchpad to confirm a Python 3.14 Windows wheel exists.
- `.venv\Scripts\python -m pytest` (full suite; the database tests 5 times in a row; the full suite with `-W error`)
- `.venv\Scripts\ruff format .` · `.venv\Scripts\ruff check --fix .`
- In the project: `reel config check` → `reel db status` → `reel db init` (twice) → `reel db status` → `reel db upgrade` → `reel config check`.

**Tests performed and results**
- `pytest`: **167 passed** (71 new: 36 models, 13 engine, 16 versioning, 6 CLI). With warnings as errors (`-W error`): 167 passed, no warnings. The database tests passed 5 times in a row.
- Covered: all 14 tables are created. Every entity is stored and read back with all fields, including Armenian text, JSON, dates, and UTC times from a Yerevan time. The PRD Publication fields are mapped. A unique content hash is enforced, also against upper case and spaces; an empty hash is allowed before import; invalid hashes are rejected. One job per recording. Foreign keys are enforced. Unique rows per job, a unique watcher path and username (case-insensitive). CHECK constraints work. Deleting a recording deletes its job and all job rows in the database itself, and watcher rows keep their history. ORM delete of a job works. Ids are not reused after a delete. Naive datetimes are rejected. UTC storage and comparisons are correct. Enums are stored as values and unknown values are rejected. JSON keeps Armenian unescaped and rejects NaN. Job flags are saved. A stale job update raises `StaleDataError`. Invalid cut lists are rejected (8 cases). Stage order and names match D-012/FR-5.
- Engine: WAL (also visible to a plain sqlite3 connection, and the `-wal` file is used), `foreign_keys = 1`, `busy_timeout = 30000`, `synchronous = 2`. Opening a missing file fails and creates nothing. Folders with spaces and Armenian letters work. Commit and rollback; a savepoint rolls back only its part. Objects stay readable after commit. A reader is not blocked by an open write transaction. A deferred read-then-write fails after a concurrent commit, which is why `immediate=True` exists. 4 threads × 25 read-modify-write transactions with `immediate=True` → exactly 100, no "database is locked". After `dispose()` the file can be renamed (not held open).
- Versioning: init creates the file and its folder, a version row and an instance id. A second init does nothing and keeps the id. An empty file is initialised. A foreign database is refused and left byte-for-byte unchanged. A non-database file and a folder are reported unreadable. Inspection never creates or changes a file. Open requires an initialised current database. A newer schema is refused by open, init and upgrade, and the file is unchanged. An outdated schema must be upgraded first. Upgrade makes a backup, applies migrations 2 and 3 once each, and a second upgrade does nothing. A failing migration leaves the schema unchanged, because the DDL is rolled back. A missing migration step is refused before any backup. Upgrade creates tables that are new in the code. A backup can be made of a database in use.
- CLI: `status` before init → exit 1 with advice; `init` → created, a second `init` → nothing to do; `status` → current, wal, instance id, row counts; `upgrade` → already current, no backup. `paths.database_file` and `paths.data_dir` move the database. A broken database file and a broken config are reported. `config check` shows warn → ok → error for missing → initialised → garbage.
- `ruff check` / `ruff format --check`: clean.
- Manual run in the project: `reel config check` before init → 0 errors, 2 warnings (database not created yet, consent registry); `reel db init` → "Created database …\data\reel.db (schema version 1)."; again → "already exists … nothing to do"; `reel db status` → current, wal, 132 KB, 14 tables (app_meta 2, schema_versions 1, others 0); `reel db upgrade` → "already at schema version 1"; `reel config check` → `[ OK ] database data\reel.db: schema version 1`, 0 errors, 1 warning (consent registry, expected until T-601).
- Acceptance criteria: schema created in a temporary database — pass; insert and read every entity — pass; unique content hash per Recording — pass; one Job per Recording — pass; WAL mode confirmed — pass. `reel db init` and the schema version table — pass.

**Problems encountered and how they were resolved**
- The PRD PDF could not be opened by the file reader (missing `pdftoppm`). Solved without installing anything: a stdlib-only text extractor in the scratchpad.
- While designing, it became clear that the import stage hashes the file after the watcher has created the recording (T-202/T-204). The content hash is therefore nullable but unique once set. Duplicates point to the original instead of storing the same hash.
- The first draft of the "stale job update" test would have hit SQLite's "database is locked" (a stale read snapshot) instead of the ORM's `StaleDataError`. It was rewritten to model the real case (staff loaded the job earlier; a worker changed it since). A separate test now shows the SQLite behaviour that `transaction(immediate=True)` avoids.
- Ruff: one import-order fix; the formatter re-wrapped 4 files.
- Observation: after a read-only inspection, SQLite leaves `reel.db-wal` (empty) and `reel.db-shm` (32 KB) next to the database. This is normal for WAL mode. These files belong to the database and are documented in the README.

**Decisions**
- D-027 (database conventions). Beyond the plan, and small: `reel db status`, `reel db upgrade` with backup, the database line in `reel config check`, and the `paths.database_file` setting.

**Remaining issues**
- None for T-101. The job state transitions (claim, lease, retry, hold, release, re-run) come in T-102 and use `transaction(immediate=True)`.

**Next task**
- T-102 — Job state machine and persistent queue. **Waiting for the user's approval.**

### 2026-11-18 19:36 — T-004 Structured logging — DONE

**What was done**
- `src/reel/logging_setup.py` (standard library `logging` only, no new dependency):
  - `JsonFormatter`: one JSON object per line with `time` (UTC, ms, `Z`), `level`, `component`, context fields (`job_id`, `recording_id`, `stage`, `attempt`, `worker`, in this order), `message`, extra fields (sorted; names that clash with core fields get an `extra_` prefix), `exception`, `stack`. Armenian is written as-is (UTF-8).
  - `TextFormatter`: console `19:36:25 INFO    [job 14 | transcribe] reel.demo: message (key=value)`; job logs add the date.
  - `log_context(**fields)` / `current_context()` on `contextvars`, plus a `ContextFilter` that copies the context onto every record (explicit `extra=` wins).
  - `JobFileHandler`: `data/jobs/<job id>/job.log`, append-and-close per record, safe job ids only.
  - `Redactor` + `register_secret()`: masks secret values and well-known token formats in all outputs (D-026).
  - `configure_logging()` (idempotent; console, rotating JSON file `logs/reel-<role>.jsonl`, job files; quiets noisy libraries; routes Python warnings to logging), `configure_from_config(cfg, role=...)` (uses the config, masks the `.env` secrets), `reset_logging()`.
- Config: new `[logging]` section (level, console_level, file_max_mb = 10, file_backups = 10, job_logs, quiet_loggers) in the schema and `config/reel.example.toml` (example = defaults still enforced by a test).
- CLI: global `-v/--verbose`; every command now has terminal logging (WARNING by default, DEBUG with `-v`).
- `tests/conftest.py`: an autouse fixture resets logging and forgets secrets after each test.
- **Fix in T-003 code:** `ConfigProvider` now detects changes by content hash instead of modification time + size (see "Problems").

**Files created / modified**
- Created: `src/reel/logging_setup.py`, `tests/unit/test_logging.py`.
- Modified: `src/reel/config/schema.py` (`LoggingSettings`, `LogLevel`), `src/reel/config/loader.py` (content-hash change detection), `src/reel/cli.py` (`-v`, logging setup), `config/reel.example.toml` (`[logging]`), `tests/conftest.py`, `TASKS.md`, `PROGRESS.md`, `DECISIONS.md` (D-026, D-025 reload note), `ARCHITECTURE.md` (§6, status line), `README.md`.

**Commands executed**
- `.venv\Scripts\python -m pytest` (full suite; and the config + logging tests 5 times in a row)
- `.venv\Scripts\ruff format .` · `.venv\Scripts\ruff check .`
- A demo with the real configuration, with log and data folders redirected to the session scratchpad so no fake job folders are left in the project.

**Tests performed and results**
- `pytest`: **96 passed** (20 new logging tests). Covered: JSON field names and order, UTC time format, values; exception with traceback; Armenian text; extra named like a core field; context nesting and restore; no context leak to another thread; explicit extra wins; console line format and level filtering; job log contains only its own job, with date and stage; unsafe job id cannot escape the jobs folder; job logs can be switched off; secrets masked in console, JSON and job log (configured value, runtime-registered value, URL `access_token=`, `password` field, exception text `token=`, Slack token, Bearer token); short values and token counts not masked; rotation configured (`RotatingFileHandler`, maxBytes, backupCount) and working (3000 lines → exactly `.jsonl`, `.1`, `.2`); configuring twice gives no duplicate output; noisy libraries quieted; case-insensitive level names; `configure_from_config` uses the project folders and masks `.env` secrets (including comma-separated API tokens); CLI `-v` shows debug messages and the default hides them.
- Repeated runs: the config + logging tests passed 5 times in a row (56 passed each time).
- `ruff check` / `ruff format --check`: clean.
- Demo output (real config): console `19:36:25 INFO    [job 14 | transcribe] reel.demo: Transcribed Դաս 14 (audio_seconds=6720.5 segments=412)`; JSON `{"time": "2026-11-18T15:36:25.554Z", "level": "INFO", "component": "reel.demo", "job_id": 14, "stage": "transcribe", "message": "Transcribed Դաս 14", "audio_seconds": 6720.5, "segments": 412}`; the registered Slack token appeared as `[REDACTED]` in all three outputs; `job.log` contained only job 14's lines.
- Acceptance criteria: JSON fields — pass; job-context propagation — pass; redaction of secret values — pass; rotation configured — pass (and exercised).

**Problems encountered and how they were resolved**
- The full run exposed a real weakness in T-003's `ConfigProvider`: it detected changes by modification time + size. Windows file times advance in ~15 ms steps, so two quick edits of equal size (`count = 0` → `count = 3`) looked unchanged, and the second edit was ignored (the T-003 test passed earlier only by timing luck). Fixed by comparing a content hash (BLAKE2b) of the watched files. They are a few KB, so the cost is negligible. Verified with 5 repeated runs.

**Decisions**
- D-026 (logging conventions). Deviation from the plan text: the JSON log is `logs/reel-<role>.jsonl` (one per process role) instead of a single `logs/reel.jsonl`, because on Windows two processes cannot rotate the same file. TASKS.md updated.

**Remaining issues**
- None for T-004. Long-running commands start using `configure_from_config()` in T-103 (worker) and T-901 (serve).

**Next task**
- T-101 — Database layer and models. **Waiting for the user's approval.**

### 2026-11-18 19:25 — T-003 Configuration system — DONE

**What was done**
- New runtime dependencies (all free): pydantic 2.14.3 (MIT), python-dotenv 1.2.4 (BSD-3-Clause), tzdata 2026.6 (Apache-2.0, time zones for `zoneinfo` on Windows). pydantic also installed annotated-types, typing-extensions and typing-inspection (MIT).
- Package `src/reel/config/`:
  - `schema.py`: pydantic models for 19 sections, 100+ settings: paths, ingest, workers, stt, llm, cleaning.silence/boundaries/breaks/cuts, audio, render, metadata, consent, youtube, slack, classroom, library, notifications, web, schedule, retention, budget. Every setting has a default and range/format checks. Also cross-field rules (e.g. heavy stage limit ≤ workers, http provider needs a URL, live notifications need a channel), template placeholder checks, regex checks (exactly one group for the lesson number, named year/month/day groups for dates) and a time-zone check. There are no secret fields, and YouTube privacy is not configurable (D-017).
  - `groups.py`: group mapping with case- and separator-insensitive matching (also for Armenian) and aliases; duplicates are rejected.
  - `loader.py`: file discovery, TOML + `.env` reading, `REEL__SECTION__KEY` overrides, path resolution relative to the project folder, readable errors naming the setting (and the environment variable when relevant), and `ConfigProvider` (reload on change, keep the last good config on a bad edit).
  - `prompts.py`: prompt files with `$placeholders`, read on every use.
  - `secrets.py`: fixed list of secret names, `Secrets` whose repr/str never shows values, secrets needed by the enabled features.
  - `check.py` + `commands.py`: `reel config check | init | show`.
- `src/reel/cli.py`: sub-command structure, global `--config`, and UTF-8 output so Armenian text never crashes redirected output on Windows.
- Example files: `config/reel.example.toml` (every setting commented, with PRD references), `config/groups.example.toml` (group "Python 12" from the brief), `.env.example` (6 secret names, all empty), `config/prompts/README.md` (prompt format).
- Ran `reel config init` in the project: created the local, git-ignored `config/reel.toml`, `config/groups.toml` and `.env` from the examples.

**Files created / modified**
- Created: `src/reel/config/{__init__,schema,groups,loader,prompts,secrets,check,commands}.py`, `config/reel.example.toml`, `config/groups.example.toml`, `config/prompts/README.md`, `.env.example`, `tests/conftest.py`, `tests/unit/test_config_loading.py`, `tests/unit/test_config_parts.py`, `tests/unit/test_config_cli.py`; local (not in git): `config/reel.toml`, `config/groups.toml`, `.env`.
- Modified: `src/reel/cli.py`, `pyproject.toml` (dependencies), `TASKS.md`, `PROGRESS.md`, `DECISIONS.md` (D-025), `ARCHITECTURE.md` (§7, status line), `README.md` (setup steps).

**Commands executed**
- `.venv\Scripts\python -m pip install -e ".[dev]"`
- `.venv\Scripts\python -m pytest` · `.venv\Scripts\ruff format .` · `.venv\Scripts\ruff check .`
- `reel config init` (twice) · `reel config check` · `REEL__WORKERS__COUNT=0 reel config check` · `reel config show` (via a Python subprocess, to verify the UTF-8 JSON output)

**Tests performed and results**
- `pytest`: **76 passed** (53 new). Covered: the example config loads, with paths relative to the project; the example equals the code defaults; a short config uses defaults; invalid value → message with setting name and value; unknown key rejected without echoing its value; TOML syntax error with line number; missing config/groups files; 16 validation rules; no secret-like setting names; no privacy setting; environment overrides (numbers, lists, nested sections); a bad override names the variable; unknown override rejected; `.env` read, with real environment winning; secret values never in repr; config outside a `config/` folder; provider reload, last-good fallback, groups reload and first-load failure; group matching (case, `_`/`-`, aliases, Armenian); duplicate names; numeric IDs kept as text; prompts (rendering, re-read, missing value/file, invalid name); required secrets per feature; `config check` (passes with the real FFmpeg; fails for a broken file and a bad override; reports a missing folder, missing secret and empty group IDs; missing FFmpeg; accepts a live setup with its secret); `config init` never overwrites; `config show` hides secret values and prints Armenian; `reel config` without an action is a usage error.
- `ruff check` and `ruff format --check`: clean.
- Manual runs in the project: `reel config check` → exit 0, 0 errors, 1 expected warning ("consent registry config\consent.json not found: every recording will be held…", until T-601). With `REEL__WORKERS__COUNT=0` → exit 1 and the message `workers.count: Input should be greater than or equal to 1 (got 0) [set by environment variable REEL__WORKERS__COUNT]`. A second `config init` kept all files. `config show` piped output is valid UTF-8 JSON (Armenian code points verified).
- Acceptance criteria: example config loads — pass; invalid values give errors that name the key — pass; environment overrides work — pass; schema has no secret fields — pass; `reel config check` exits 0 for the example config and non-zero for a broken one — pass.

**Problems encountered and how they were resolved**
- The "no secret fields" test flagged `llm.context_tokens` ("token" in the name). The test was kept strict and the setting was renamed `llm.context_length`.
- Ruff findings (2 long lines, a nested `if`, a quoted annotation) were fixed; the formatter re-wrapped 5 files.

**Decisions**
- D-025 (configuration conventions), including the defaults chosen for open questions: time zone Asia/Yerevan, same-evening cutoff 23:59 (Q-3), title `{group} · Lecture {lesson}` (Q-13), Slack message template (Q-9). All are changeable in the config.
- `reel config init` and `reel config show` were added beyond the plan (small; they make setup and debugging easier).

**Remaining issues**
- None for T-003. Later tasks add their own checks to `reel config check` (Whisper model, Ollama, prompts).

**Next task**
- T-004 — Structured logging. **Waiting for the user's approval.**

### 2026-11-18 19:09 — T-002 FFmpeg (project-local) — DONE

**What was done**
- Chose the build: `ffmpeg-9.0.1-essentials_build.zip` (Gyan.dev essentials, GPL-3.0, free) from the official mirror `github.com/GyanD/codexffmpeg`. Its SHA-256 `fec81ae03971d9dd4be3ebe02e263bd2ec1d789483f931bdba5f5715e65da2e9` was cross-checked against two independent sources: the GitHub release asset digest and the winget-pkgs manifest `Gyan.FFmpeg.Essentials` 9.0.1. Details in D-015.
- `src/reel/media/ffmpeg_check.py`: runs `ffmpeg -filters/-encoders/-version` and `ffprobe -version`, and reports missing features. Required: 19 filters (silencedetect, freezedetect, loudnorm, ebur128, thumbnail, trim, atrim, setpts, asetpts, concat, afade, aresample, scale, pad, fps, testsrc2, sine, anoisesrc, anullsrc) and 2 encoders (libx264, aac). Reused later by `reel config check` and the media toolkit.
- `scripts/setup_ffmpeg.py`: downloads with progress to `tools/.downloads`, verifies the checksum (a bad archive is deleted), extracts only `bin/ffmpeg.exe`, `bin/ffprobe.exe`, `LICENSE`, `README.txt` (fixed names, no path traversal possible) into a staging folder, writes `INSTALL.json` (version, URL, checksum, per-file hashes), verifies the capabilities, then swaps the folder in. A failure keeps the previous install. Re-running is a no-op when the manifest and binary hashes match. Options: `--check`, `--force`, `--keep-archive`.
- Installed FFmpeg 9.0.1 into `tools/ffmpeg` (bin ≈ 98 MB each for ffmpeg.exe and ffprobe.exe). The archive and the empty download folder were removed afterwards.

**Files created / modified**
- Created: `src/reel/media/__init__.py`, `src/reel/media/ffmpeg_check.py`, `scripts/setup_ffmpeg.py`, `tests/unit/test_ffmpeg_check.py`, `tests/unit/test_setup_ffmpeg.py`, `tests/integration/test_ffmpeg_installed.py`, `tools/ffmpeg/` (downloaded; not in git).
- Modified: `TASKS.md`, `PROGRESS.md`, `DECISIONS.md` (D-015 details), `ARCHITECTURE.md` (status line), `README.md` (setup step).

**Commands executed**
- `winget show --id Gyan.FFmpeg.Essentials --exact --source winget` (checksum source 1)
- GitHub API `releases/tags/9.0.1` and `9.0.2` (checksum source 2)
- gyan.dev `release-version` / `.sha256` request → HTTP 503 (automated requests refused; not retried)
- `.venv\Scripts\python scripts\setup_ffmpeg.py` (3 runs: first failed at the folder swap, second installed, third was a no-op), `... --check`
- `tools\ffmpeg\bin\ffmpeg.exe -hide_banner -version / -encoders`, `ffprobe.exe -hide_banner -version`
- `.venv\Scripts\python -m pytest`, `.venv\Scripts\ruff check .`, `.venv\Scripts\ruff format .`

**Tests performed and results**
- `pytest`: **23 passed**, 0 skipped. 13 new unit tests: filter/encoder parsing; report of missing features; missing binary; install extracts only the needed files; second run does nothing; checksum mismatch fails and installs nothing; changed binary is detected and reinstalled; `--force`; archive without ffprobe is rejected; failed verification keeps the previous install; pinned build consistency; a busy folder move is retried; a permanently busy folder gives a clear error; a failed swap restores the previous install. 2 integration tests against the real install: all required features present; version matches `INSTALL.json`.
- `ruff check` and `ruff format --check`: clean.
- Acceptance criteria: (1) checksum mismatch fails — pass (unit test); (2) `ffmpeg -version` / `ffprobe -version` from `tools/ffmpeg` → `9.0.1-essentials_build-www.gyan.dev` — pass; (3) all required filters and encoders present — pass (also `h264_qsv` Intel Quick Sync is available for T-405); (4) re-run → "already installed - nothing to do" in 0.6 s, no download — pass.

**Problems encountered and how they were resolved**
- gyan.dev refused automated requests (HTTP 503). Resolved by downloading from Gyan's official GitHub mirror, with the checksum confirmed by two independent sources.
- First install run: the download (366 s, about 0.3 MB/s) and the checksum succeeded, but moving `tools/ffmpeg.staging` → `tools/ffmpeg` failed with `[WinError 5] Access is denied`. The probable cause is antivirus or the indexer briefly scanning the newly written .exe files. Fix: folder moves and deletes are retried for up to 30 s, with a clear error message after that; if the new build cannot be moved in, the previous build is restored. Covered by 3 new tests. The second run reused the verified archive and succeeded in 6 s.

**Decisions**
- Pinned FFmpeg 9.0.1 rather than 9.0.2, so the checksum is confirmed by two sources (D-015 updated).
- Ruff's formatter (target Python 3.14) writes `except A, B:` without parentheses (PEP 758, valid only on Python 3.14+). Accepted, because the project requires Python 3.14.

**Remaining issues**
- None for T-002. The FFmpeg paths become configurable in T-003.

**Next task**
- T-003 — Configuration system. **Waiting for the user's approval.**

### 2026-11-18 18:53 — T-001 Python environment and project skeleton — DONE

**What was done**
- Installed Python 3.14.10 with the Python install manager (`py install -y 3.14`; the installer verified the signature of the python.org index). The system default stays 3.15.1. The installer noted that its shortcut folder `C:\Users\User\AppData\Local\Python\bin` is not on PATH. PATH was left unchanged because the venv is used directly.
- Created `.venv` (Python 3.14.10) inside ReelProject, plus `.venv\pip.ini` with `no-cache-dir = true`, so pip writes no cache outside the project.
- `pyproject.toml`: setuptools, src layout, version read from `reel.__version__`, `reel` console script, dev extras (pytest, ruff), pytest and ruff settings.
- Package `src/reel`: `__init__.py` (`__version__ = "0.1.0"`), `__main__.py`, `cli.py` (`--version`, help).
- Folders: `config/prompts`, `scripts`, `tests/unit`, `tests/integration`, `docs/integrations`, `tools`, `models`, `data`, `logs`, `inbox`, `samples`.
- `.gitignore` and a README with development setup.
- Editable install with dev tools: pytest 9.2.0 and ruff 0.17.5 (both MIT, free) and their dependencies (colorama, iniconfig, packaging, pluggy, pygments).

**Files created / modified**
- Created: `pyproject.toml`, `.gitignore`, `README.md`, `src/reel/__init__.py`, `src/reel/__main__.py`, `src/reel/cli.py`, `tests/unit/test_cli.py`, `.venv/` (with `pip.ini`), the folders above.
- Modified: `TASKS.md` (T-001 status), `PROGRESS.md`, `ARCHITECTURE.md` (status line).

**Commands executed**
- `py install -y 3.14` · `py -V:3.14 -m venv .venv` · wrote `.venv\pip.ini`
- `.venv\Scripts\python -m pip install -e ".[dev]"`
- `.venv\Scripts\reel --version` · `.venv\Scripts\reel`
- `.venv\Scripts\python -m pytest`
- `.venv\Scripts\ruff check .` · `.venv\Scripts\ruff format --check .` · `.venv\Scripts\ruff check --show-files .`

**Tests performed and results**
- `pytest`: 5 passed — runtime ≥ 3.14; installed distribution version equals the package version; `--version` output; help without a command; `python -m reel --version` in a subprocess.
- `ruff check`: all checks passed. `ruff format --check`: all files formatted ("9 files" = 4 Python files + code blocks in the 5 Markdown files).
- Acceptance criteria: (1) venv Python 3.14.10 — pass; (2) `reel --version` → `reel 0.1.0` — pass; (3) smoke tests — pass; (4) ruff — pass; (5) outside ReelProject only the Python 3.14 runtime was added; the user pip cache (`%LOCALAPPDATA%\pip\Cache`, last changed 2026-10-25) was not touched; pip's temporary build folders in `%TEMP%` are removed by pip itself — pass.

**Problems encountered**
- None.

**Decisions**
- pip cache disabled for the venv, so every file stays inside the project (minor; not added to DECISIONS.md).
- The CLI has no sub-commands yet; later tasks add them (config, db, worker, serve, …).
- No licence was set for the Reel code itself. That is the academy's choice, so the question was added to the outstanding questions (needed by T-1004).

**Remaining issues**
- None for T-001.

**Next task**
- T-002 — FFmpeg (project-local). **Waiting for the user's approval.**

### 2026-11-18 18:30 — T-000 PRD analysis and development plan — DONE

**What was done**
- Inspected `C:\Users\User\Desktop\ReelProject`: it existed and was empty.
- Checked the machine (tools, CPU/RAM/GPU, disk, Docker state, winget packages).
- Read the whole PRD (11 pages) and the original brief image; listed requirements, risks and open questions.
- Asked the user 8 blocking questions in two rounds and recorded the answers (D-001 … D-008). During the session the user added two working rules: only free tools (D-009), and approval after every task (D-010).
- Checked PyPI for Python 3.15 wheel availability of the key native packages: `onnxruntime` 1.32.0 has no cp315 wheels, which led to D-011 (Python 3.14). ctranslate2, av, tokenizers, numpy, pydantic-core, pillow and greenlet do have suitable wheels.
- Wrote the plan (48 tasks in 11 phases, with a PRD traceability matrix), the decision log, the architecture baseline and this progress file.

**User answers (verbatim summary)**
| Question | Answer |
|---|---|
| Stack | Python + SQLite queue (recommended option) |
| Lesson language | Armenian |
| Speech-to-text | Local Whisper |
| LLM | Local LLM (Ollama) |
| Chatter/break detection | The lesson start is recognised by the lecturer's greeting ("hello" etc.) or phrases like "today we will talk about…", "our topic for today is…" and similar; the end by phrases like "that's all for today", "thank you, see you another time". |
| Operator UI | Web dashboard + Slack alerts |
| Consent/publishing | PRD rule (auto-publish; hold on opt-out or unreachable Core Engine) |
| Core Engine / Library | Products of other groups, not ready yet; after Reel is finished it will be given to them for merging |
| Extra rule 1 | Every tool, program and server used to build or run the product must be free |
| Extra rule 2 | After each task: report, state the next step, and continue only after approval |

**Files created**
- `TASKS.md`, `PROGRESS.md`, `DECISIONS.md`, `ARCHITECTURE.md`
- `docs/PRD_English.pdf`, `docs/PRD_Armenian.pdf`, `docs/brief_reel.png` (copies of the user's Desktop files; originals untouched)

**Commands executed (read-only checks unless noted)**
- `Get-ChildItem` on ReelProject and Desktop; `Get-Command` for python, node, ffmpeg, docker, git, redis, uv, ollama
- `python -m pip --version`, `py list`, `winget --version`, `winget show Gyan.FFmpeg`, `nvidia-smi`, `docker info`, CPU/RAM/disk queries
- A PyPI JSON query script (in the session scratchpad, not in the project) for wheel availability
- `Copy-Item` of the PRD PDFs and the brief into `docs/` (write)

**Tests performed**
- No code yet. Plan check: every FR-1 … FR-34, NFR-1 … NFR-10, UJ-1 … UJ-5 and C-1 … C-5 maps to at least one task (TASKS.md traceability matrix).

**Problems found and how they were handled**
- Docker daemon not running → not needed (single-machine SQLite design, D-001).
- No wheels for `onnxruntime` on Python 3.15 → the project will use Python 3.14 (D-011).
- FFmpeg and Ollama not installed → T-002 (project-local FFmpeg) and T-304 (Ollama, location to be confirmed).
- No GPU → local Whisper may be slow for 2–3 h recordings; risk to M-2 / same evening, to be measured in T-303.

**Decisions made**
- D-001 … D-010 from the user; D-011 … D-024 by the agent (low-risk, documented; D-013 is only a proposal).

**Remaining issues**
- See "Outstanding questions" above.

**Next task**
- T-001 — Python environment and project skeleton: install Python 3.14 via `py install 3.14`, create `.venv` in ReelProject, `pyproject.toml`, `src/reel` package with the `reel` CLI, folders, `.gitignore`, pytest and ruff, smoke test. **Waiting for the user's approval.**
