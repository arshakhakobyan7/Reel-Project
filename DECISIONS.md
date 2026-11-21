# Reel — Decisions

Important product and technical decisions, with reasons. **Source** says who decided: *user* (answer from the project owner) or *agent* (low-risk technical choice made during development; the user can override it).
Status: `Accepted` · `Proposed` (waiting for user confirmation) · `Superseded`.

| ID | Decision | Source | Status |
|---|---|---|---|
| D-001 | Python + FastAPI + SQLite-backed persistent job queue | user | Accepted |
| D-002 | Lessons are in Armenian; generated text in Armenian | user | Accepted |
| D-003 | Speech-to-text: local faster-whisper | user | Accepted |
| D-004 | LLM: local model through Ollama | user | Accepted |
| D-005 | Lesson start/end found from the lecturer's phrases | user | Accepted |
| D-006 | Operator UI: web dashboard + Slack alerts | user | Accepted |
| D-007 | Publishing/consent policy of the PRD (A-5, A-9) | user | Accepted |
| D-008 | Core Engine and Academy Library: ports + contracts + interim implementations | user | Accepted |
| D-009 | Everything used must be free | user | Accepted |
| D-010 | One task at a time, user approval after each task | user | Accepted |
| D-011 | Project venv on Python 3.14 (not the system 3.15) | agent | Accepted |
| D-012 | Stage order: transcript first; separate Consent stage | agent | Accepted (follows D-005) |
| D-013 | Break detection: break/resume phrases + long low-speech gap | agent | Superseded by D-040 |
| D-014 | Silence is cut only where the picture is also static | agent | Accepted |
| D-015 | FFmpeg kept inside the project, checksum-verified | agent | Accepted |
| D-016 | Dry-run mode for every external integration | agent | Accepted |
| D-017 | YouTube privacy hard-coded to `unlisted` | agent | Accepted |
| D-018 | Identification by folder/file name; hold before Chapters if missing | agent | Accepted |
| D-019 | Unknown consent status counts as opted out | agent | Accepted |
| D-020 | "Cost" of local providers = compute time and tokens | agent | Accepted |
| D-021 | Source recordings are never deleted by default | agent | Accepted |
| D-022 | Slack bot token (not webhook); Classroom announcement by default | agent | Accepted |
| D-023 | Documentation in English; conversation with the user in Armenian | agent | Accepted |
| D-024 | Polling folder watcher instead of OS file events | agent | Accepted |
| D-025 | Configuration conventions (file discovery, overrides, .env, reload) | agent | Accepted |
| D-026 | Logging conventions (per-role JSON files, UTC, redaction, job logs) | agent | Accepted |
| D-027 | Database conventions (SQLite settings, transactions, ids, schema versions) | agent | Accepted |
| D-028 | Job queue rules (claim order, leases, crash = attempt, release modes, re-run limits) | agent | Accepted |
| D-029 | Worker runtime (stage contract, stage threads, cooperative stop, timeouts, notifications) | agent | Accepted |
| D-030 | Media validation and test media (probe + decode start/end, atomic audio extraction) | agent | Accepted |
| D-031 | Second developer (GPT / Codex) for isolated tasks | agent | Accepted |
| D-032 | Import stage and duplicates | agent | Accepted |
| D-033 | Transcript format | agent | Accepted |
| D-034 | Local Whisper engine and the transcribe stage | agent | Accepted |
| D-035 | Lessons recorded in several parts | user | Accepted |
| D-036 | Default speech model and settings (measured) | agent | Accepted |
| D-037 | Output resolution at most 1080p | user | Accepted |
| D-038 | Local LLM: Ollama in the project, gemma3:4b on the integrated GPU | agent | Accepted |
| D-039 | Lesson boundaries: anchored phrases first, LLM only with a verified quote | agent | Accepted |
| D-040 | Nobody at the board for more than 10 s → shortened to 5 s (replaces D-013) | user | Accepted |
| D-041 | Cut list rules (padding only at lesson boundaries, parts as one timeline, frame-aligned) | agent | Accepted |
| D-042 | Renderer: Quick Sync by default (quality 26), CPU fallback, two-pass loudness | agent | Accepted |
| D-043 | Stage `clean`: order of the analyses, LLM on demand, parts, transcript remap rules | agent | Accepted |
| D-044 | Chapters: LLM labels checked against the text, grouping without LLM, starts at transition phrases | agent | Accepted |
| D-045 | Stage `chapters`: identification gate, main topic on the thumbnail, thumbnail optional | agent | Accepted |
| D-046 | Stage `consent`: holds per outcome, unreachable retried then held, release audit on the check row | agent | Accepted |
| D-047 | OAuth token in Windows Credential Manager (ctypes, no new package); loopback + PKCE sign-in | agent | Accepted |
| D-048 | Upload/post stages: a row per target, retries repeat only unpublished targets, posts are previews while YouTube is in dry-run, quota waits for the reset | agent | Accepted |
| D-049 | Dashboard: Armenian UI; stdlib scrypt passwords, HMAC-signed session cookie, per-session CSRF token, login throttling | user (language) + agent | Accepted |
| D-050 | Metrics: cost = processing time + LLM tokens; success metrics over a period; budget flag + staff alert once per job | agent | Accepted |
| D-051 | Dashboard upload: resumable pieces into data/uploads, finished lessons in data/recordings and queued directly (not via the watcher); staff values win | agent (user's questions answered with defaults) | Accepted |
| D-052 | `reel serve` runs everything in one process; leases of dead local Reel processes end at start; one notifier; job commands in the CLI | agent | Accepted |
| D-053 | Read-only integration API with bearer tokens (`REEL_API_TOKENS`), id paging, no private data | agent | Accepted |
| D-054 | Retention: working files, transcripts and stale uploads expire; sources only if enabled; events stay append-only | agent (Q-11 defaults) | Accepted |
| D-055 | Autostart with a per-user Task Scheduler task (pythonw, restart on failure); no Docker image | agent | Accepted |
| D-056 | The PC's sleep: stage timeouts and processing time count only awake time; no idle sleep while a stage runs; own leases survive a long sleep | agent | Accepted |
| D-057 | A Done video that is not on YouTube is never removed by retention; the recording page plays and downloads the cleaned video | user + agent | Accepted |
| D-058 | Board framing: the whole board and all the work stay in the picture, with a small margin; one crop per camera; applied in the render | user + agent | Accepted |
| D-059 | The LLM never cuts the lesson's start or end alone: the line it names must hold a configured phrase | user | Accepted |
| D-060 | The dashboard on the local network: HTTPS from Reel itself, with its own name-constrained CA | user + agent | Accepted |
| D-061 | A group typed in the dashboard is added to groups.toml at once; the transcript is not shown on the recording page | user | Accepted |
| D-062 | English programming terms stay English in the transcript: a term fixer after Whisper; no prompt in every window | user + agent | Accepted |

---

### D-001 — Technology stack
- **Date:** 2026-11-18 · **Source:** user · **Status:** Accepted
- **Context:** The PRD names no language or queue technology. Python 3.15 and Docker Desktop exist on the machine; Node.js and Redis do not.
- **Decision:** Python; FastAPI web dashboard; a persistent job queue stored in SQLite (WAL mode). No extra server software is needed.
- **Alternatives:** Python + Celery/RQ + Redis + PostgreSQL (scales across servers, but needs Docker and Celery/RQ do not run natively on Windows); Node.js/TypeScript + BullMQ + Redis.
- **Consequences:** Single-server deployment. SQLite can be replaced by PostgreSQL later through SQLAlchemy if several servers are needed.

### D-002 — Lesson language
- **Date:** 2026-11-18 · **Source:** user (answer to PRD Q-2) · **Status:** Accepted
- **Decision:** Lessons are taught in Armenian (often with English technical terms). Whisper runs with language fixed to `hy`. Chapters and descriptions are generated in Armenian (translation is out of scope, PRD §4.3).
- **Consequences:** Phrase lists for lesson boundaries are in Armenian; the LLM model must handle Armenian; the thumbnail font must contain Armenian glyphs.

### D-003 — Speech-to-text provider
- **Date:** 2026-11-18 · **Source:** user (answer to PRD Q-7) · **Status:** Accepted
- **Decision:** Local faster-whisper (free, MIT licence). Audio never leaves the machine (mitigates R-9).
- **Alternatives:** OpenAI Whisper API, ElevenLabs Scribe (paid cloud services).
- **Consequences:** No GPU on this PC (Intel i5-1335U, 16 GB RAM), so transcription of a 2–3 h lesson can take hours. This puts M-2 (≤ 3 h) and "same evening" at risk on this machine. To be measured in T-303.

### D-004 — LLM provider
- **Date:** 2026-11-18 · **Source:** user (answer to PRD Q-7) · **Status:** Accepted
- **Decision:** A local open-weights model served by Ollama (free, MIT). Model chosen in T-304 after an Armenian test.
- **Consequences:** Long transcripts must be processed in chunks (map-reduce) because of local memory limits. Output quality on Armenian must be checked; strict validation and fallbacks (FR-19) are essential.

### D-005 — Lesson start/end detection
- **Date:** 2026-11-18 · **Source:** user (answer to FR-7/FR-8/Q-14 question) · **Status:** Accepted
- **Decision (user's words, summarised):** the lesson **starts** when the lecturer greets the class ("hello" or another greeting) or says phrases like "today we will talk about…" or "our topic for today is…" and similar expressions; the lesson **ends** with phrases like "that's all for today" or "thank you, see you another time".
- **Implementation:** configurable Armenian (+ English) phrase lists with fuzzy matching on the transcript, and the local LLM for "similar expressions" that are not in the lists. If nothing is found confidently, nothing is cut and the job is flagged (protects against R-1).
- **Consequences:** the transcript must exist before cutting → D-012.

### D-006 — Operator interface
- **Date:** 2026-11-18 · **Source:** user (answer to PRD Q-15) · **Status:** Accepted
- **Decision:** Web dashboard with staff login (status by stage, errors, cut list, hold release/cancel, re-run from stage) plus Slack notifications for failures and holds.

### D-007 — Publishing and consent policy
- **Date:** 2026-11-18 · **Source:** user (confirmed PRD A-5, A-9; answers Q-1 and Q-6 for the MVP) · **Status:** Accepted
- **Decision:** Publishing is automatic, with no human review. If any student in the group opted out of photo/video, or the consent source cannot be reached, the job goes On hold and staff are notified. Staff check the video and release or cancel it. Face blurring stays in Phase 2.

### D-008 — Core Engine and Academy Library
- **Date:** 2026-11-18 · **Source:** user · **Status:** Accepted
- **Context:** User: "These are products from other groups and are not yet ready. Once we finish, we will provide our product to them for merging."
- **Decision:** Reel talks to both systems only through small interfaces (`ConsentProvider`, `LibraryPublisher`). For each one Reel ships: a proposed REST contract for the other team (`docs/integrations/`), an HTTP client implementing it, a working interim implementation (a consent registry file; a local library inside Reel), and a mock server for tests. A read-only integration API (T-806) helps the merge.
- **Consequences:** The real integrations are switched on by configuration once the other teams are ready; the contracts may change after discussion with them.

### D-009 — Free tools only
- **Date:** 2026-11-18 · **Source:** user · **Status:** Accepted
- **Rule:** Every tool, program, server and service used to build Reel, and used by Reel itself, must be free.
- **Applied so far:** Python, FFmpeg, faster-whisper and Whisper models, Ollama and open-weights models, SQLite, FastAPI and the other Python libraries (open-source licences), YouTube Data API, Slack API, Google Classroom API (free to use; quota limits apply). Paid STT/LLM APIs are excluded. Docker Desktop is not needed and not used. THIRD_PARTY.md (T-1004) will list every dependency with its licence.

### D-010 — Working process
- **Date:** 2026-11-18 · **Source:** user · **Status:** Accepted
- **Rule:** After completing each task: tell the user what was done, what was tested and what comes next, then **wait for approval** before starting the next task.

### D-011 — Python 3.14 for the project environment
- **Date:** 2026-11-18 · **Source:** agent · **Status:** Accepted
- **Context:** The system Python is 3.15.1. faster-whisper requires `onnxruntime` (for voice activity detection), and onnxruntime 1.32.0 publishes Windows wheels only up to Python 3.14 (checked on PyPI on 2026-11-18).
- **Decision:** Install Python 3.14 with the Python install manager (`py install 3.14`) and create the project venv `.venv` with it.

### D-012 — Stage order and the Consent stage
- **Date:** 2026-11-18 · **Source:** agent (direct consequence of D-005) · **Status:** Accepted
- **Decision:** `Imported → Transcribed → Cleaned → Chapters → Consent → Uploaded → Posting links → Done`.
  - The PRD shows `Cleaned → Transcribed`. Because the lesson boundaries are found from speech (D-005), the raw recording is transcribed first (silences are skipped by voice activity detection, so the extra work is small). Then the transcript timestamps are mapped onto the cleaned video. The result is the same as transcribing the cleaned audio (FR-13), but transcription runs only once.
  - The consent check (FR-20) gets its own stage before upload (as in UJ-1 step 6). Its result and any hold are then visible and auditable.

### D-013 — Break detection (proposal)
- **Date:** 2026-11-18 · **Source:** agent · **Status:** Superseded by D-040 (the user's rule, 2026-11-19)
- **Context:** The user described how the lesson start and end are recognised, but not the break (FR-8, Q-14).
- **Proposal:** Same idea as D-005. Break-start phrases ("ընդմիջում", "դադար", "10 րոպե …") and resume phrases ("շարունակենք", "վերադառնանք") are combined with the longest low-speech gap in the middle of the lesson. If there is no clear evidence, nothing is cut and the job is flagged.

### D-014 — Silence removal only over a static picture
- **Date:** 2026-11-18 · **Source:** agent (mitigation for R-1) · **Status:** Accepted
- **Decision:** A silence longer than the threshold (FR-6) is removed only if the picture is also static during it (FFmpeg `freezedetect`). During quiet live coding the screen changes, so that content is kept. Configurable (can be switched off).

### D-015 — FFmpeg inside the project
- **Date:** 2026-11-18 · **Source:** agent · **Status:** Accepted
- **Context:** The user asked for all dependencies to live inside ReelProject. FFmpeg is not installed on the PC.
- **Decision:** A setup script downloads the free Gyan.dev Windows build, verifies its SHA-256 and extracts it to `tools/ffmpeg/` (not committed). Paths are configurable. On a server, a system FFmpeg can be used instead.
- **Details (T-002):** pinned build `ffmpeg-9.0.1-essentials_build.zip` (111,253,802 bytes) from the official mirror `github.com/GyanD/codexffmpeg`. SHA-256 `fec81ae0…5e65da2e9` matched two independent sources on 2026-11-18: the GitHub release asset digest and the winget-pkgs manifest `Gyan.FFmpeg.Essentials` 9.0.1. Version 9.0.2 had only one checksum source, so 9.0.1 was chosen. The gyan.dev site answered automated requests with HTTP 503, so it is not used by the script. Upgrading = change `PINNED_BUILD` after checking the new checksum the same way.

### D-016 — Dry-run mode for external integrations
- **Date:** 2026-11-18 · **Source:** agent · **Status:** Accepted
- **Decision:** YouTube, Slack, Classroom, Core Engine and Library integrations each have a dry-run mode that records what would be sent without calling the service. They are enabled one by one when credentials or the real systems exist.

### D-017 — Unlisted enforced in code
- **Date:** 2026-11-18 · **Source:** agent (PRD C-1, NFR-5) · **Status:** Accepted
- **Decision:** The YouTube privacy status is a constant `unlisted` in the code, not a configuration value, so configuration cannot publish a video publicly.

### D-018 — Identification of group and lesson
- **Date:** 2026-11-18 · **Source:** agent (implements PRD A-4; Q-5 still open for confirmation) · **Status:** Accepted
- **Decision:** `<watched folder>/<group folder>/<file>`. The group is the folder name (case-insensitive, with aliases in `groups.toml`). The lesson number comes from the file name through configurable patterns (`Lecture 14`, `L14`, `Դաս 14`). The date comes from the file name, else the media creation time, else the file time. If the group or number is missing, heavy processing continues, the job holds before Chapters (the title needs them), staff are notified, and they can set the values in the dashboard. Nothing is guessed.
- **Details (T-203, 2026-11-19):** Q-5 confirmed by the user: staff put each recording into the group's folder and name it; a missing lesson number waits for staff (no guessing). `reel.ingest.identify` searches the folders between the outermost watched folder and the file, nearest first, so `<group>/<sub folder>/<file>` also works. Every pattern match in the file name is collected: two different numbers give `ambiguous_lesson_number`. Flags: `unknown_group`, `missing_lesson_number`, `ambiguous_lesson_number`, `missing_date`. Dates: file name (`2026-11-19` and `19.11.2026` by default; impossible dates are skipped), else the media creation time (ignored before 2000 or in the future), else the file time, both as a local date in `schedule.timezone`. How each value was found is stored in `Recording.identification`.

### D-019 — Unknown consent status
- **Date:** 2026-11-18 · **Source:** agent (FR-21 safety) · **Status:** Accepted
- **Decision:** A student whose consent status is missing or unknown is treated as opted out, which causes a hold.

### D-020 — Cost tracking with free local providers
- **Date:** 2026-11-18 · **Source:** agent (NFR-8 under D-003, D-004, D-009) · **Status:** Accepted
- **Decision:** Local Whisper and Ollama have no money cost. Reel tracks per recording the compute time per stage and the LLM token counts. The budget limit (NFR-8) is a maximum processing time per recording, with an alert when it is exceeded.

### D-021 — Retention of source recordings
- **Date:** 2026-11-18 · **Source:** agent (NFR-6; Q-11 still open) · **Status:** Accepted
- **Decision:** Reel's own working files are deleted after a configurable period. The original recordings in the watched folder are never deleted unless retention for them is explicitly configured.

### D-022 — Slack and Classroom posting method
- **Date:** 2026-11-18 · **Source:** agent (Q-9 partially; to confirm with the user before T-703/T-704) · **Status:** Accepted
- **Decision:** Slack posts use a bot token (`chat.postMessage`), not an incoming webhook, because the PRD's Publication entity needs the message link and duplicate protection needs the message id. Classroom posts are announcements with a link by default; course material can be configured.

### D-023 — Languages of documents and communication
- **Date:** 2026-11-18 · **Source:** agent · **Status:** Accepted
- **Decision:** Code, comments and project documents are in English (like the PRD), so the other teams and future sessions can read them. Conversation with the user is in Armenian. The UI language of the dashboard will be confirmed with the user before T-802.

### D-024 — Polling folder watcher
- **Date:** 2026-11-18 · **Source:** agent · **Status:** Accepted
- **Decision:** The watcher scans the folders every few seconds instead of relying on OS file-change events. It is simpler and works the same for local, network and cloud-synced folders. The cost of a scan is negligible for a few folders.
- **Details (T-202):** a file is queued when its size and modification time have not changed for `ingest.stable_seconds` and it is not empty. Paths are stored case-insensitively on Windows. A file deleted before it was stable becomes `gone`; if it comes back, it waits again. A queued file that changes later counts as a new file; the import stage's content hash still stops duplicate processing. A watch folder that is missing (an unplugged drive) is reported, and its files are left as they are. The recording's `detected_at` is the first sighting of the file (start of M-2).

### D-025 — Configuration conventions
- **Date:** 2026-11-18 · **Source:** agent (T-003) · **Status:** Accepted
- **Decision:**
  - **File discovery:** `--config PATH`, else `$REEL_CONFIG`, else `./config/reel.toml`. Relative paths in the settings are relative to the project folder, which is the parent of the `config/` folder (or `paths.base_dir`).
  - **Defaults:** every setting has a default in code; `config/reel.example.toml` documents all of them with the same values, and a test keeps the two equal.
  - **Strictness:** unknown keys are errors (catches typos and secrets placed in TOML). Error messages name the setting and the value. The value of an unknown key is never printed, because it could be a secret.
  - **Overrides:** environment variables `REEL__SECTION__KEY=value` (values parsed as TOML, e.g. numbers or lists). Errors name the variable.
  - **Secrets:** only from the environment or `.env`; real environment variables win over `.env`. A fixed list of secret names (`SECRET_NAMES`); `reel config check` reports which ones the enabled features need. Secret values never appear in `repr`/`str`/`config show`.
  - **Reload (NFR-9):** `ConfigProvider.get()` re-reads `reel.toml`, `groups.toml` and `.env` when one of them changes. An invalid edit is rejected: the last valid configuration stays in use, and the error is logged and kept in `last_error`. A change is detected by a content hash, not by file time; changed in T-004 after a test showed two quick equal-size edits within one Windows clock tick (~15 ms) were missed.
  - **Prompts:** text files `config/prompts/<name>.txt` with `$placeholders` (`string.Template`), read on every use.
  - **Defaults chosen for open questions:** time zone `Asia/Yerevan` (assumption: the academy is in Armenia; configurable), "same evening" cutoff 23:59 (Q-3), title `{group} · Lecture {lesson}` (Q-13), Slack message `🎬 {title} / {summary} / {url}` (Q-9). All can be changed in the config without code changes.
  - **Naming:** settings must not contain words like "token" or "secret" (a test enforces this). For that reason the LLM context size is called `llm.context_length`.

### D-026 — Logging conventions
- **Date:** 2026-11-18 · **Source:** agent (T-004) · **Status:** Accepted
- **Decision:**
  - **One JSON log per process role:** `logs/reel-<role>.jsonl` (e.g. `reel-serve.jsonl`, `reel-worker.jsonl`) instead of a single `reel.jsonl`. On Windows a file cannot be rotated while another process has it open, so processes must not share a rotating file. Rotation by size (`logging.file_max_mb`, `logging.file_backups`).
  - **Times:** JSON logs use UTC (`…Z`) so they are unambiguous; console and job logs use local time for people.
  - **Job logs:** `data/jobs/<job id>/job.log`, opened and closed per record. Nothing stays locked, and several processes can append. Only job ids made of letters, digits, `_`, `-` are accepted, so a job id cannot write outside the jobs folder.
  - **Context:** `log_context(job_id=…, stage=…)` uses `contextvars`. It applies to the current thread only, and explicit `extra=` values win.
  - **Redaction (NFR-5):** every output is passed through a redactor. It masks the secret values from `.env`/environment (each part of comma-separated token lists too; values shorter than 6 characters are ignored) and secrets registered at runtime (`register_secret`, for OAuth tokens in T-701). It also masks well-known formats: Slack `xox…-`, Google `ya29.…` and `1//…`, `Bearer …`, `password=`/`token=`/`client_secret=`/… assignments, and string values of fields whose names look secret. Numeric fields such as token counts are not masked.
  - **Short CLI commands** log to the terminal only (WARNING, or DEBUG with `-v`). Long-running commands (worker, serve; T-103/T-901) call `configure_from_config()`, which adds the JSON and job files.
  - Only the standard library `logging` is used (no new dependency).

### D-027 — Database conventions
- **Date:** 2026-11-18 · **Source:** agent (T-101) · **Status:** Accepted
- **Decision:**
  - **Library and file:** SQLAlchemy ORM on SQLite. Version 2.1 (MIT), the current stable series of the 2.x API; the plan named 2.0. The file is `paths.database_file`, by default `<data_dir>/reel.db`. It must be on a local disk: SQLite's WAL mode does not work on network shares.
  - **Connection settings:** WAL journal, so readers (dashboard) never wait for the writer (workers). Foreign keys enforced. `synchronous = FULL`: a committed record of an upload or post survives a power cut, and Reel writes too little for the extra disk syncs to matter. Busy timeout 30 s.
  - **No accidental empty database:** connections open the file in `rw` mode. Only `reel db init` creates a database, so a mistyped path fails with a clear message.
  - **Transactions:** SQLAlchemy sends BEGIN itself (the recipe in the SQLAlchemy documentation for the sqlite3 driver's legacy mode). `transaction()` starts a normal (deferred) transaction. `transaction(immediate=True)` starts `BEGIN IMMEDIATE`. That form is required for read-then-write work such as claiming a job (T-102). A deferred transaction that has read data cannot write after another connection committed: SQLite refuses at once with "database is locked", and a test demonstrates this. Objects stay readable after commit (`expire_on_commit = False`).
  - **Times:** only time-zone-aware datetimes are accepted. They are stored as UTC and read back as UTC. Naive datetimes are rejected, because their zone is unknown and mixing them would shift lease and retry times by +4 h.
  - **Enums:** stored as their lowercase values and validated in Python. There are no CHECK constraints on them, so adding a value later needs no table rebuild (SQLite cannot change constraints in place). Fixed numeric rules (sizes, attempts, versions not negative) do have CHECK constraints.
  - **JSON columns:** UTF-8, so Armenian text stays readable. Compact. NaN and infinity are rejected. A JSON value is replaced, not changed in place, because the ORM does not notice in-place changes. `Job.add_flag()` follows this rule.
  - **Ids are never reused:** AUTOINCREMENT on `recordings`, `jobs` and `library_entries`, because these ids appear in folder names, logs and idempotency tags. Every database also gets a random `instance_id` (`app_meta`). T-702 … T-706 will include it in the tags they attach to YouTube videos and Slack posts. A re-created database restarts its ids at 1, and the instance id stops its new jobs from being mistaken for the old database's uploads.
  - **One job per recording:** a unique `recording_id`; a re-run reuses the job (FR-32). Jobs have a row `version`: an ORM save based on stale data fails with `StaleDataError` instead of silently overwriting a change made by another process.
  - **Content hash (FR-4):** unique, lowercase SHA-256 hex. It stays empty until the import stage has hashed the file (T-204 hashes after the watcher has created the recording). A duplicate keeps an empty hash and points to the original through `duplicate_of_id`.
  - **Publication:** stored as one row per target (`publication_targets`), not as the PRD's single Publication row. FR-29 needs a status, attempt count and error for each target. `Job.publication_summary()` returns the PRD's fields (video id, playlist id, Slack link, Classroom post id, Library entry id).
  - **Cut lists:** a re-run of cleaning adds a new version, so earlier versions stay available when a wrong cut is investigated (UJ-5). Segments are validated when stored: ordered, not overlapping, `keep` or `remove`, each with a reason (FR-9).
  - **Source of truth:** transcripts, cut lists and metadata live in the database (FR-14). Files in `data/jobs/<id>/` are media and optional exports.
  - **Deletion:** deleting a recording or a job deletes its rows in the other tables through `ON DELETE CASCADE` in the database itself. Watcher rows stay, with `recording_id` set to empty.
  - **Schema versions:** the `schema_versions` table holds one row per applied version. `reel db init` creates the current schema. `open_database()` refuses a missing, older, newer or foreign database and says what to do. `reel db upgrade` first backs up the file into `data/backups/` (SQLite online backup). It then applies the migrations, one version per transaction (SQLite DDL is transactional, so a failed step changes nothing), and creates any tables that are new in the code. This is a small built-in runner instead of Alembic: no extra dependency or configuration files. Switching to Alembic remains possible if migrations become frequent.
- **Alternatives:** Alembic for migrations (heavier for one SQLite file); plain `sqlite3` without an ORM (more code for the same result).

### D-028 — Job queue rules
- **Date:** 2026-11-18 · **Source:** agent (T-102) · **Status:** Accepted
- **Decision:**
  - **One transaction per operation**, started with `BEGIN IMMEDIATE`. "Find the oldest due job" and "mark it running" happen under the database write lock, so two workers can never get the same job, whether they are threads or separate processes. Tested with 6 threads and with 3 processes.
  - **Order:** the oldest job (lowest id) that is due runs first. Older recordings therefore finish first, which serves the same-evening goal. Jobs waiting for a retry delay are skipped until they are due.
  - **Leases and stale workers:** a claim leases the job to one worker for `workers.lease_seconds`, and the worker renews it with heartbeats (T-103). Each attempt is identified by its StageRun. A worker call with an old claim is refused with `LeaseLost` and changes nothing. The claim is old when staff cancelled the job, when its lease expired, or when a newer attempt exists, even one by the same worker name. A late result from a hung worker can therefore never overwrite newer work.
  - **A crash costs one attempt:** an expired lease is recovered by `claim()` itself or by `recover_expired()`, and counts as a failed attempt. The retry delay counts from the moment the lease ran out, so restarting after a long outage adds no extra wait.
  - **A graceful stop costs nothing:** `interrupt()` gives the job back at once without using an attempt.
  - **Retries (FR-31):** delay = `base × factor^(attempt − 1)`, capped by the new setting `workers.backoff_max_seconds` (default 3600 s). Without a cap, a large `max_attempts` would give absurd delays, and could overflow the date range. Fatal errors (`retryable=False`) fail at once.
  - **Holds and release (UJ-3):** a stage holds the job with a reason code (e.g. `consent_opted_out`) and a message. Staff release it in one of two ways: run the held stage again (e.g. after setting the group, D-018), or accept the stage and continue with the next one (e.g. after checking the video on a consent hold). The dashboard (T-804) offers the way that fits the reason.
  - **Re-run (FR-32):** possible for failed, done, cancelled, on-hold and queued jobs, from any stage the job has already reached. It can never go forward past an unfinished stage, because that would skip a stage. Earlier stages are not repeated; the chosen stage and the ones after it run again.
  - **Cancel:** possible for queued, running, on-hold and failed jobs. A running stage learns about it at its next heartbeat (`LeaseLost`), and its result is discarded.
  - **Heavy-stage limit:** checked inside the claim transaction, by counting the running heavy stages, so the limit also holds across processes. Used by T-103.
  - **Audit:** every transition writes a JobEvent (kind, actor kind system/worker/staff and name, stage, message, data) and is logged with the job id, so it also appears in the per-job log. Every stage attempt has a StageRun, which the ending transition closes as succeeded, failed, held, interrupted or cancelled. Staff actions that start no stage (release, re-run, retry now, cancelling a queued job) write a JobEvent only. The queue never deletes history.
  - **Clock:** the queue takes a clock function, and tests use a fake clock. Failed and cancelled jobs get `finished_at`; a re-run clears it.

### D-029 — Worker runtime
- **Date:** 2026-11-19 · **Source:** agent (T-103) · **Status:** Accepted
- **Decision:**
  - **Stage contract:** a stage is a `StageHandler` class with `name` (a pipeline stage), `heavy`, an optional `timeout_seconds` and `run(ctx)`. Returning means success, optionally with a `StageResult` (metrics and a short message for the StageRun and the event). `Hold(reason, message)` puts the job on hold. `StageFailed(message, retryable=...)` is an expected failure. Any other exception is an unexpected failure, retried like a retryable one, with the traceback in the job log. Stages must be safe to run again (NFR-2).
  - **Threads:** `reel worker` runs `workers.count` slots in one process. Each stage attempt runs in its own thread, so the slot thread stays free to renew the lease every third of `workers.lease_seconds`, to enforce the timeout and to notice a staff cancel (the heartbeat gets `LeaseLost`).
  - **Cooperative stop:** Python cannot kill a thread. The runtime asks the stage to stop, and long stages call `ctx.check()` or `ctx.wait()`. External processes are registered with `ctx.on_cancel()`, so FFmpeg can be killed (T-201). If a stage does not stop within `workers.stop_grace_seconds` (default 30 s), it is abandoned: the job is handled at once and whatever the stage returns later is ignored.
  - **Timeout:** the stage's `timeout_seconds`, else `workers.stage_timeout_minutes`. A timeout is a retryable failure.
  - **Graceful stop:** Ctrl+C, Ctrl+Break or SIGTERM asks running stages to stop and gives their jobs back without using an attempt (`interrupt`). A stage that finishes during the grace time is completed normally.
  - **Notifications (FR-33):** when a job fails for good (fatal error or last attempt) or goes on hold, the notifier hook is called. A scheduled retry is not reported. The default notifier writes a warning to the logs; Slack comes with T-801. A failing notifier never stops the worker.
  - **Settings without restart (NFR-9):** the configuration is read again before every claim. Everything except `workers.count` applies without a restart.
  - **Names:** slot names are `<computer>-<process id>-<slot>`, shown as the lease owner.
  - **Stages without an implementation** are not claimed; their jobs wait in the queue. That is all 7 stages until T-204 adds the first one, and `reel worker` says so at start.
  - **`--until-idle`:** stops when no queued or running job is left at a stage the worker can run. Jobs waiting for a retry are waited for; held, failed and finished jobs are not. Used by scripts and the end-to-end tests (T-1001).
  - Two new settings: `workers.idle_poll_seconds` (how often an idle slot looks for jobs, default 5 s) and `workers.stop_grace_seconds`.

### D-030 — Media validation and test media
- **Date:** 2026-11-19 · **Source:** agent (T-201) · **Status:** Accepted
- **Decision:**
  - **Validation (FR-2):** ffprobe must read the file; it needs a video stream, an audio stream and a known duration of at least `ingest.min_duration_minutes`. Then only the first and the last 3 s are decoded (`-xerror`, and at least one frame is required). A full decode of a 3-hour file would take too long, while a truncated or damaged file shows its problem at the end. Each failure has a readable reason.
  - **Runner:** FFmpeg runs with `-progress pipe:1`. Progress is reported in seconds and as a fraction. Cancelling kills the process through `StageContext.on_cancel` and raises `FfmpegCancelled`. Errors carry the last lines of FFmpeg's error output.
  - **Audio extraction:** the first audio track becomes 16 kHz mono 16-bit WAV. It is written as `.part` and renamed when complete, so a crash never leaves a half file and a re-run is safe.
  - **Test media:** `reel.media.testmedia` (also `scripts/make_test_media.py`) builds lessons from parts. Each part has a moving (`testsrc2`) or static picture and a noise (speech stand-in), tone or silence. Only FFmpeg's built-in sources are used, so no media files are stored in the project.

### D-031 — Second developer (GPT / Codex) for isolated tasks
- **Date:** 2026-11-19 · **Requested by:** the user
- **Decision:** Isolated, well-specified tasks are given to GPT (ChatGPT Pro / Codex in VS Code) through prompt files in `handoff/` (`handoff/README.md` holds the common rules). GPT only creates the files listed in its task file, never reads `samples/`, `.env`, `data/` or local configs, and writes `handoff/T-xxx.report.md`. Claude reviews the code and runs the full test suite before a task is marked DONE, then applies any requested shared changes (config, DB, CLI, docs).
- **First batch:** T-401, T-601, T-703, T-705. Core pipeline, security (OAuth, web auth), real-recording work and documentation of record stay with Claude.

### D-032 — Import stage and duplicates
- **Date:** 2026-11-19 · **Source:** agent (T-204; FR-2, FR-4, FR-5) · **Status:** Accepted
- **Decision:**
  - Order: streamed SHA-256 first (4 MiB chunks, cancellable), then validation, then media info and identification, then `data/jobs/<id>/audio.wav`. Each step is safe to repeat.
  - The duplicate check and saving the hash happen in one `BEGIN IMMEDIATE` transaction, so two copies imported at the same moment cannot both pass. A duplicate's job is **cancelled** (not failed: staff are not alerted) with "duplicate of recording N (job M, path)", and `Recording.duplicate_of_id` points to the original. To process the content again, staff re-run the original job.
  - A stage now ends a job this way by raising `Discard(message)`; the worker calls the new `JobQueue.discard()` (event `cancelled`, actor = worker).
  - An unusable file fails at once (not retried) and notifies staff; a missing or unreadable file and FFmpeg not running are retried.
  - Identification is re-applied on a re-run unless `Recording.identification["set_by_staff"]` is set (the dashboard, T-804, will set it).

### D-033 — Transcript format
- **Date:** 2026-11-19 · **Source:** agent (T-301; FR-13, FR-14) · **Status:** Accepted
- **Decision:** `reel.stt.Transcription` = language + segments in start order (overlaps allowed, as Whisper produces them) + model + details. A segment has start/end seconds, text, optional word timings and Whisper's `avg_logprob` / `no_speech_prob` (kept for hallucination checks, R-4). Times are finite, non-negative and kept to the millisecond. Words are stored as compact `[start, end, text, probability]` lists. JSON (`schema: 1`, UTF-8, Armenian readable) is written atomically; the database keeps one row per job and kind (`raw`, `clean`), replaced on a re-run.
- **Fake engine:** `FakeTranscriber` returns a scripted Armenian lesson stretched to the audio length (chatter, greeting and topic, break, resume, closing, chatter; positions in `LESSON_MARKS`) or given segments, so later stages are tested without a speech model.

### D-034 — Local Whisper engine and the transcribe stage
- **Date:** 2026-11-19 · **Source:** agent (T-302; FR-14, R-4, D-002) · **Status:** Accepted
- **Decision:**
  - faster-whisper 1.2.1 (MIT) with CTranslate2 4.9 (MIT), onnxruntime (MIT, Silero VAD), PyAV (BSD), tokenizers and huggingface-hub (Apache-2.0). Models: free Systran conversions of Whisper (MIT) in `models/whisper/<model>`, downloaded once by `scripts/setup_whisper.py` (its download cache also stays in `models/whisper/.hf-cache`). At run time nothing is downloaded; a missing model fails the job at once with the command that fixes it.
  - Audio is read by Reel (`load_wav`, 16 kHz mono from the import stage) and passed as samples, because PyAV 19 broke faster-whisper's own decoder (`metadata_errors`). This also keeps Reel independent of PyAV changes.
  - Against invented text (R-4): VAD on, `condition_on_previous_text=False`, `hallucination_silence_threshold=2` s with word timestamps, and runs of the same segment text repeated more than twice are dropped (count kept in details).
  - Prompt = `[stt] initial_prompt` + the group's `vocabulary`. Loaded models are cached for the next job; one transcription per model at a time.
  - Progress: stages call `ctx.report_progress(done, total)`; each heartbeat saves the latest value in `StageRun.metrics["progress"]`, and the stage logs every 10 %. An empty result adds the job flag `empty_transcript`.

### D-035 — Lessons recorded in several parts
- **Date:** 2026-11-19 · **Source:** user · **Status:** Accepted
- **Context:** the real sample lesson is two iPhone files (`IMG_0359.MOV` 47.7 min, `IMG_0360.MOV` 52.7 min, 4K HEVC); without joining, each would become its own video.
- **Decision:** parts of one lesson are uploaded together through the dashboard (T-807): all files chosen in one upload are one lesson, and confirming the upload means no more parts will come. Parts are ordered by recording time (editable in the form). Reel makes one recording and one job; the timeline is the parts one after another. A file dropped into a watched folder is still one complete lesson.

### D-036 — Default speech model and settings (measured)
- **Date:** 2026-11-19 · **Source:** T-303 benchmark on the user's real lesson (this PC: Intel i5-1335U, 10 cores / 12 threads, 16 GB) · **Status:** Accepted
- **Decision:** `large-v3-turbo`, int8, beam 1, no temperature fallback, VAD on, word timestamps off, threads = all logical CPUs but 2. Benchmark table and estimate in PROGRESS.md (T-303 entry).
- **Why:** `small` and `medium` are not usable for Armenian (mostly wrong words; `medium` falls into repetition loops without fallback). `large-v3` is only slightly better than turbo but 3× slower. Beam 5 and temperature fallback cost time without a visible gain (fallback re-decodes unsure Armenian windows up to six times). Word timestamps add about 55 % to the transcription time; segment times are enough for boundaries, and cuts are placed at detected silences.
- **Filters kept without fallback:** windows whose text compresses more than 2.4× (repetition loops) are dropped by Reel.
- **Risk to M-2 (same evening):** a 2 h 41 min lesson needs about 3 hours on this laptop before upload (see the estimate). To be reduced in T-405 with Intel Quick Sync (free, built into the CPU) for decoding and encoding, and re-measured in T-1002.

### D-037 — Output resolution at most 1080p
- **Date:** 2026-11-19 · **Source:** user · **Status:** Accepted
- **Decision:** the published video is at most 1080 pixels high (`render.max_height = 1080`). 4K and larger recordings (like the iPhone sample) are scaled down, keeping the aspect ratio; smaller recordings keep their size. Implemented in T-405.

### D-038 — Local LLM: Ollama in the project, gemma3:4b on the integrated GPU
- **Date:** 2026-11-19 · **Source:** T-304 test on the user's real lesson (machine transcript) · **Status:** Accepted
- **Install (user's choice):** Ollama v0.40.16 (MIT), portable build checked against its SHA-256, in `tools/ollama`; models in `models/ollama`; Ollama's own files in `data/ollama-home` (`scripts/setup_ollama.py`). Reel starts this server itself when nothing answers at a local `llm.base_url` (`OllamaServer`); nothing is installed system-wide or written to the user profile (checked).
- **Model:** `gemma3:4b` (Gemma terms: free, open weights). Compared with `qwen3:8b` on the same Armenian task (title, summary, chapters from 10 min of transcript): gemma3:4b on the GPU 66–103 s, 4.3–6 tokens/s, 2537 prompt tokens, correct topic ("User-Defined Function-ների ստեղծում և օգտագործում"); qwen3:8b 193 s, 1.8 tokens/s, 4336 prompt tokens (Armenian costs it 70 % more tokens), topic right but it copies the transcript's misspellings. qwen3:8b was deleted afterwards. Chapter titles of both are weak: T-501 must improve the prompts (group vocabulary, window summaries) and staff can edit (T-804).
- **GPU:** `llm.use_igpu = true` sets `OLLAMA_IGPU_ENABLE=1` so Ollama uses the Intel Iris Xe (Vulkan): about 45 % faster than the CPU here and the CPU stays free for Whisper and FFmpeg.
- **Client:** chat API with streaming (`check()` between chunks), the answer's pydantic JSON schema as `format`, validation of every answer, `think: false`, retries with backoff for timeouts, connection and server errors and invalid answers; a missing model fails at once with the command that installs it.
- **Memory note:** with the user's other programs open only 1–3 GB of the 16 GB were free; gemma3:4b needs about 4 GB while loaded (unloaded after 5 min idle).

### D-039 — Lesson boundaries: anchored phrases first, LLM only with a verified quote
- **Date:** 2026-11-19 · **Source:** T-402 tests on the user's real lesson (first 30 min of part 1, last 30 min of part 2) · **Status:** Accepted
- **Matching:** Armenian-aware normalisation (case, marks inside words, «և» = «եւ», punctuation) plus phonetic folding of letters Whisper confuses (օ/ո, է/ե, ֆ/վ, ռ/ր, բ/փ/պ, գ/ք/կ, դ/թ/տ, ձ/ց/ծ, ջ/չ/ճ); fuzzy threshold `fuzzy_threshold` (0.85). Without folding two real phrases were missed.
- **Start:** a greeting at most `anchor_window_minutes` before a topic opener. A topic opener alone is **not** enough: the real lesson starts with a 19-minute recap and «այսօր մենք անցնելու ենք…» comes only at 19:17 — cutting there would have removed teaching. Otherwise the LLM decides.
- **End:** the last closing phrase, cut at the end of its segment (real lesson: found at 1798 of 1800 s). Otherwise the LLM.
- **LLM guard:** the answer must quote the line's own words; Reel checks the quote is really in that line (0.8 similarity) and otherwise rejects it. gemma3:4b invented a greeting for the real start and picked a wrong line with garbled words for the real end — both rejected, so nothing was cut. The LLM gets at most 8000 characters: the first lines of the start window, the **last** lines of the end window.
- **No confident evidence → no cut** + flag `lesson_start_not_found` / `lesson_end_not_found`; start ≥ end → no cut + `lesson_boundaries_conflict`. Staff can set the boundaries by hand (T-804). LLM errors are raised so the stage retries.

### D-040 — Nobody at the board for more than 10 s → shortened to 5 s
- **Date:** 2026-11-19 · **Source:** user (answer to D-013 / Q-14) · **Status:** Accepted
- **Rule (user):** the break is usually between the parts of a lesson, but not always. Whenever nobody is at the board for more than 10 seconds, that stretch is shortened to 5 seconds — **also when someone is heard** (the user chose this over keeping stretches with speech). It replaces the phrase-based break proposal D-013 and removes every empty stretch, not only the break.
- **How:** about one picture per second (`sample_seconds`) is checked by a person detector: YOLOX-tiny (Megvii, Apache-2.0, ONNX 20 MB, `models/person`, `scripts/setup_person_model.py`, pinned SHA-256) on the CPU through onnxruntime (already installed with faster-whisper). Only the "person" class counts, figures shorter than `min_person_height` (8 % of the picture) are ignored. When keyframes are at least as dense (iPhone: every 0.83 s), only keyframes are decoded, on the GPU (`hwaccel = "auto"` → d3d11va, CPU if it fails); otherwise `fps` sampling.
- **Cut:** a run of samples that saw nobody (score < `person_score` 0.4), longer than `min_empty_seconds` (10), keeps `keep_seconds` (5): half at each side (the lecturer leaving and coming back). A single detection splits a run; a gap in the samples is never cut across; nobody until the end reaches the end of the video.
- **Measured on the user's lesson:** part 1 (47.7 min) 3357 pictures in 137 s, part 2 (52.7 min) 3720 in 157 s (≈0.05 × real time; GPU decoding is 2.5× faster than the CPU). The lecturer was found in 7073 of 7077 pictures (moving along the board, ≈35 % of the picture height); the only empty moment (1615.8–1617.5 s, checked by eye: the board really is empty) is under 10 s → nothing cut. The break was not recorded (it is between the two files).

### D-041 — Cut list rules
- **Date:** 2026-11-19 · **Source:** agent (T-404; FR-9, FR-10, FR-12, D-035, D-040) · **Status:** Accepted
- **One raw timeline:** the parts of a lesson one after another (`Timeline`); every analysis reports removals on it. Segments are split at part edges so each one lies inside one file; the join between parts is not a cut.
- **Padding (`padding_seconds`) only next to the lesson-boundary cuts** (before/after the lesson), never at the very start or end. Silence and empty-board removals already follow their own rules; padding them again would break the user's "shorten to 5 s" (D-040).
- **Merging and minimums:** overlapping/touching removals merge (reasons joined); a removal shorter than `min_remove_seconds` is dropped unless it touches the start or end; a kept piece shorter than `min_keep_seconds` is removed with its neighbours ("too short to keep").
- **Frames:** every boundary is put on the nearest frame of its part (`fps` from ffprobe).
- **Mapping:** raw → clean never goes backwards (a time inside a cut maps to the cut); clean → raw → clean is the identity; final duration = sum of kept segments. Nothing kept → error.
- **Storage:** `cutlist.json` (schema 1, atomic) and a new `cut_lists` row per run (version + 1); the parts are kept in `parameters`.
- **Real lesson:** parts 2860.393 s + 3159.922 s (23.998 fps); with the end found by T-402 the cut list keeps 6018.823 of 6020.315 s, the join is seamless.

### D-042 — Renderer: Quick Sync by default, CPU fallback, two-pass loudness
- **Date:** 2026-11-19 · **Source:** agent (T-405; FR-10, FR-11, NFR-4, D-036, D-037) · **Status:** Accepted
- **Graph:** one FFmpeg filter graph in a file (`-/filter_complex`): every part is an input, each kept segment is trimmed (`trim`/`atrim`), gets 30 ms audio fades (`cleaning.cuts.fade_ms`), all are joined by `concat` (it re-aligns audio and video at every segment, so no drift). Audio 48 kHz stereo AAC; `+faststart` MP4; written as `.part`, then renamed.
- **Encoder (`render.video_encoder = "auto"`):** the whole chain on the Intel GPU (Quick Sync decode → `scale_qsv` → `h264_qsv`) and libx264 on the CPU if that fails or cannot be used (rotated parts or parts of different shapes). Measured on 60 s of the user's 4K HEVC: CPU decode + libx264 veryfast 21 s; GPU decode + libx264 28 s; GPU decode + Quick Sync with CPU scaling 21 s; **all on the GPU 6.9 s (3× faster)**.
- **Quality:** `qsv_quality = 26` (ICQ). On 30 s of chalk writing: 23 → 2.36 Mbps, SSIM 0.9726; **26 → 1.1 Mbps, SSIM 0.9717**; 28 → 0.68 Mbps, 0.9707 (libx264 crf 21: 1.58 Mbps, 0.9859 — part of the gap is the different scaler). At 26 the chalk is clearly readable (checked by eye), and the file is about half the size → faster YouTube upload.
- **Loudness:** pass 1 measures the cleaned audio (`loudnorm` print, audio only), pass 2 normalises with the measured values (`linear=true`; FFmpeg switches to dynamic when the gain would break the true-peak limit). Almost silent audio (< −70 LUFS) is not normalised.
- **Size (D-037):** the shorter side is scaled down to `render.max_height` (1080), never up; even numbers.
- **Real lesson (both parts, 100.3 min of 4K, quality 23):** 969 s (0.16 × real time) with Quick Sync; 1920×1080; video 6018.79 s for a cut list of 6018.82 s; loudness −28.9 → −16.17 LUFS, true peak −1.5 dBTP (dynamic mode: the +12.9 dB gain needed the limiter). Audio ends 0.11 s after the video (AAC padding + the last frame of a variable-frame-rate source); sync inside is kept by `concat`.

### D-043 — Stage `clean`
- **Date:** 2026-11-19 · **Source:** agent (T-406; FR-6 … FR-13) · **Status:** Accepted
- **Order:** lesson start/end from the raw transcript → empty board (per part) → silences over a static picture (per part) → cut list → render → clean transcript. The cut list row, the clean transcript (linked to that cut list) and the flags are stored in one transaction at the end, so a failed render leaves no half result; `cutlist.json`, `cleaned.mp4`, `transcript.clean.json` and `cleaning.json` (why each part was cut, UJ-5) are in the job folder.
- **LLM on demand:** the project's Ollama is started only if the phrases are not enough for the lesson start/end, and stopped again before the heavy work (it needs ~4 GB of the often 1–3 GB free memory).
- **Parts (D-035):** the files of a multi-part lesson are read from `recording.media_info["parts"]` (`[{"path": ...}, ...]`, to be filled by T-807); otherwise the one source file. Per-part analyses are moved onto the joined timeline.
- **Transcript remap:** with word times a word stays when its middle is kept (the segment text is rebuilt when words were dropped); without word times (default) a segment stays when at least half of it is kept. Times are mapped through the cut list, so segments at a cut are clipped.
- **Errors:** a missing raw transcript, missing files, a missing person model or "nothing would be kept" fail at once (not retried); FFmpeg and LLM errors are retried.
- **Metrics:** original/final/removed seconds, removed seconds per reason, encoder, size, loudness, seconds per step, real-time factor; the job event says "H:MM:SS cut to H:MM:SS (N s removed)".

### D-044 — Chapters: checked LLM labels, grouping without the LLM, exact starts
- **Date:** 2026-11-19 · **Source:** agent (T-501; FR-15, FR-16, FR-19, R-3) · **Status:** Accepted
- **Why:** on the user's real lesson gemma3:4b gave some good topic labels («Immutable type-երի դերը», «Սեփական ֆունկցիաներ») but also invented Armenian words («Վարկածի դասի ուսումը», «վարկածային ֆունկցիաներ») and, asked to merge, returned 7 chapters instead of 3–5.
- **Map:** windows of `metadata.window_minutes` (3); a transition phrase (`metadata.chapter_start_phrases`, e.g. «այսօր անցնելու ենք», «հիմա անցնենք») always opens a new window. Per window the LLM gives a label and 1–3 words copied from the text. The label is used only if a copied word is really in the window (fuzzy, Armenian letter folding) and at least half of its words are in the window, its neighbours or the course vocabulary; otherwise the window has no label (copied fragments were tried as labels and were useless, e.g. «հասկացա»).
- **Group (no LLM):** neighbouring windows merge by word overlap until there are `min_chapters` … one per 12 minutes (max 12); a window opened by a transition phrase is merged last. **Start** = the group's first segment, i.e. exactly the transition phrase when there is one.
- **Titles:** one LLM call per chapter from its labels; a title not supported by the labels/text (or too long) is replaced by the most central label.
- **Rules and fallback:** YouTube's rules through `check_chapters` (T-502); no valid list → no chapters and the flag `chapters_not_generated` (staff can add chapters, T-804). An unreadable answer for a window is skipped; when most windows fail the error is raised so the stage retries.
- **Topic summary:** «Թեմաներ՝ …» built from the checked chapter titles (or the most frequent checked labels); no extra LLM call, nothing new invented.
- **Real lesson (first 30 min):** 0:00 recap topic, 9:30 / 6:20 data structures, **19:17 «Սեփական ֆունկցիաներ…»** exactly at «այսօր մենք անցնելու ենք …»; 150–200 s for 30 minutes (≈ 9–11 min for a 100-minute lesson). Titles vary a little between runs but stay within the transcript's words.

### D-045 — Stage `chapters`
- **Date:** 2026-11-20 · **Source:** agent (T-504; FR-3, FR-15 … FR-19, D-018) · **Status:** Accepted
- **Identification gate:** a group that is not in `groups.toml` or a missing lesson number puts the job on hold with reason `identification` and a message naming what to set; staff are notified; after they set the values and release the job the stage runs again. The date is optional (only used in the footer).
- **Texts:** chapters and the topic summary (D-044); title from `metadata.title_template` (`{topic}` = the title of the longest chapter, used only when staff put it in the template); description = summary + chapter list + footer (T-502; its flags go to the job).
- **Thumbnail:** the main topic as the big text and the video title below it; if the font cannot draw the topic, the title alone; if FFmpeg cannot make it, the flag `thumbnail_not_made` — the video is still published (FR-18 is optional).
- **Storage:** one `video_metadata` row per job (a re-run replaces it) with the generator details (LLM calls, windows, groups, topic, flags, seconds), and `metadata.json` in the job folder. The local LLM is started only when the clean transcript has text and stopped right after. Staff edits of the texts (T-804) must be protected from a re-run there.

### D-046 — Stage `consent`
- **Date:** 2026-11-20 · **Source:** agent (T-602; FR-20 … FR-22, UJ-3, C-3, D-019) · **Status:** Accepted
- **Every check is a `consent_checks` row:** time, provider, group reference (the group's `core_engine_group_id`, else its name), the students' opaque ids and statuses only, result, message — the audit trail.
- **Outcomes:** passed → upload. Opted out / unknown (D-019) → On hold `consent_opted_out` / `consent_unknown` with a staff notification. Unreachable → retried like other failures (`workers.max_attempts`), On hold `consent_unreachable` on the last attempt instead of failing, so staff see it.
- **Staff decisions (existing queue actions):** release with "accept" (`advance=True`) continues to upload and writes the staff name, time and note on the job's latest check row in the same transaction; release "check again" runs the stage again (a new row, no acceptance recorded); cancel ends the job, nothing is published. A held job is never claimed, so nothing reaches upload while on hold.
- A job without a known group is held as `identification` here too (safety net; normally the chapters stage holds it).

### D-047 — Secrets store and Google OAuth
- **Date:** 2026-11-20 · **Source:** agent (T-701; NFR-5, R-7) · **Status:** Accepted
- **Store:** the Google refresh token is kept in **Windows Credential Manager** (generic credential `<auth.store_namespace>/google/oauth`, encrypted by Windows for the signed-in user) through `advapi32` with `ctypes` — the `keyring` package named in the plan is not needed (D-009: no new dependency). `auth.store = "file"` keeps it in `data/secrets.dev.json` for development only (a warning on every use). Static secrets (Slack token, API tokens, the client file path) stay in the environment/`.env`.
- **Sign-in (`reel auth google`):** Google's installed-app flow with a "Desktop app" client: browser sign-in answered on `http://127.0.0.1:<random port>`, PKCE (S256) and a random `state`, `access_type=offline` + `prompt=consent` so a refresh token is always returned. Scopes: `youtube.upload`, `youtube`, `classroom.announcements`, `classroom.courseworkmaterials`. Missing granted scopes are reported. `reel auth status [--check]` and `reel auth forget`. Nothing secret is printed or logged (tokens, client secret and the code are registered for redaction).
- **Tokens for the publishers:** `GoogleTokens` implements T-702's `AccessTokens`: a cached access token, refreshed 2 minutes before expiry, `invalidate()` after HTTP 401, a rotated refresh token is stored. `invalid_grant` → `AuthError` (not retryable) "run 'reel auth google' again" → the stage fails and staff are notified; Google outages are retryable. `GoogleApiClient` now passes this error through instead of a generic one.
- **Manual steps:** `docs/setup/google.md` (project, APIs, consent screen — publish the app, otherwise an External app in Testing loses access after 7 days — Desktop client, `.env`, sign-in, quota, the R-5 private lock and the audit form, revoking).

### D-048 — Upload and posting stages
- **Date:** 2026-11-20 · **Source:** agent (T-706; FR-23 … FR-29, NFR-2, G-3) · **Status:** Accepted
- **Records:** every target (YouTube, Slack, Classroom, Library) has its own `publication_targets` row with its status, attempt count, last error, link and the publisher's saved state. Every write first checks that the attempt still holds the job, so an attempt that lost its lease cannot overwrite the newer attempt's records.
- **Upload:** the cleaned video with the stored title, description and thumbnail, and the group's playlist. Every YouTube state snapshot is saved at once, so a crash resumes the same upload session. The YouTube marker tag is `reel-<first 12 hex of the database instance id>-job-<id>`, so a new database cannot pick up another database's video. A used-up daily quota (`quotaExceeded`, `uploadLimitExceeded`) makes the next attempt wait until the quota resets, at midnight Pacific Time plus 10 minutes. For this, `StageFailed` gained `retry_after` and the queue waits at least that long, at most two days. The `youtube_not_unlisted` flag (R-5) notifies staff when the upload completes. Other upload flags are only shown in the dashboard.
- **Posting:** one stage for Slack, Classroom and Library.
  - A failing target does not stop the others. Each failure is recorded on its row.
  - The stage fails while any target failed. It is retryable if any of the failures is retryable, and the wait is the longest hint a service gave.
  - A retry repeats only the targets that are not done for the same input (a hash of the post's content, place and mode). An earlier post is updated, never posted again.
  - The job is Done when every target is published or skipped. When the attempts run out, the job is Failed, and the rows keep what was published.
  - A target is **skipped** when the group has no Slack channel or Classroom course in `groups.toml`.
  - The Slack and Classroom posts carry the database `instance_id` (D-027).
- **Dry-run:** a dry-run target is `published` with `dry_run = true`, with no id or link, and its preview is kept in `details`.
  - While YouTube has no real video, there is no link to share. Every post is then a preview, whatever its own mode, and the previews show the link `https://www.youtube.com/watch?v=reel-dry-run`.
  - A dry-run never replaces a real publication: its preview is stored beside the real one (`details.preview`).
  - With all integrations in dry-run, a job goes through every stage to Done.

### D-049 — Dashboard foundation and staff login
- **Date:** 2026-11-20 · **Source:** user (UI language), agent (T-802; FR-30, NFR-5) · **Status:** Accepted
- **Language:** the dashboard is in **Armenian** (the user's answer). The texts are in the templates and in `reel.web.texts`.
- **Stack:** FastAPI with server-rendered Jinja2 pages and plain CSS. There is no JavaScript and no Node.js.
  - `reel serve` runs the dashboard with uvicorn. By default it listens only on this computer (127.0.0.1:8080).
  - If it listens on another address without an `https://` `web.base_url`, Reel logs a warning.
  - New dependencies (all free): fastapi (MIT), jinja2 (BSD-3), python-multipart (Apache-2.0) and uvicorn (BSD-3); httpx (BSD-3) is used only for tests.
- **Passwords:** stored as `hashlib.scrypt` hashes (N=2^15, r=8, p=1, a random salt).
  - A password has 10 to 1024 characters.
  - A login with an unknown user takes as long as one with a wrong password, and both get the same message.
  - `reel user add|password|disable|enable|list` manage the accounts. The password is asked twice, or read with `--password-stdin`.
- **Sessions:** an HMAC-SHA256-signed cookie (`HttpOnly`, `SameSite=Lax`, `Secure` with an https base URL).
  - The cookie holds the user id, the issue and expiry times (`web.session_hours`, default 12), the CSRF token and a fingerprint of the password hash.
  - Every request checks the account in the database, so disabling an account or changing its password ends its sessions at once.
  - A new session starts at login, against session fixation.
  - The signing secret is `REEL_WEB_SESSION_SECRET`. When that is empty, Reel makes a random secret once and keeps it in the secret store (D-047).
- **CSRF:** every POST must carry the session's token, in the form field `csrf_token` or the `X-CSRF-Token` header. The check is a dependency of the whole app, so a new form cannot forget it.
- **Other protections:**
  - After 5 failed logins for one username or one address within 15 minutes, logins are refused for 15 minutes (in memory).
  - After login, the visitor is sent back only to a page of this site.
  - Security headers: a strict CSP (no scripts), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`, and `Cache-Control: no-store` on pages.
  - There are no API documentation pages.
- **Recordings list (T-803):** the start page lists the recordings, newest first, 50 per page.
  - Each row shows: "group · Դաս N" (or the file name), the 7 stage pills as in the brief (done, current state, not started), the status, the original → final duration, local times (`schedule.timezone`), the last error or the hold message, and the flags in Armenian (an unknown code is shown as it is).
  - Status counts at the top work as filters; there are also status and group filters (including "Առանց խմբի", no group).
  - The page reloads itself every 30 s with `<meta http-equiv="refresh">`, so no JavaScript is needed.
- **Recording detail (T-804):** `/recordings/<id>` shows the stage pills, the hold or error, the facts, the actions, and:
  - the cut-list bar as an SVG (blue kept, grey removed), drawn with SVG attributes because the CSP allows no inline styles, with the removed time per reason in Armenian;
  - chapters, title, summary, description and the thumbnail (served only from inside the data folder);
  - publications (only `http(s)` links are shown as links), consent checks with the staff decision, stage runs, and the event history. (The first 60 transcript lines were shown here too, until the user asked to remove them on 2026-11-20, D-061.)
- **Actions** are POST forms with the CSRF token. They call the queue, which writes an event with the staff name and note, then go back to the page with an Armenian message (an invalid one → 409):
  - release a hold: "accept and continue" or "check again";
  - cancel;
  - set the group (from `groups.toml`), lesson number and date. This is the new `JobQueue.identify`: it removes the identification flags, keeps the old and new values in the event, and releases an `identification` hold. It is not possible while a stage runs;
  - re-run from a stage the job has reached;
  - retry now.
  Editing the title or description is not one of these actions, so a re-run cannot overwrite a staff edit.

### D-050 — Metrics, compute "cost" and the time budget
- **Date:** 2026-11-20 · **Source:** agent (T-805; NFR-7, NFR-8, M-1 … M-6, D-020, Q-3 default) · **Status:** Accepted
- **Per recording:** the processing time is the sum of its stage runs' wall-clock time, counting every attempt, per stage and in total.
  - Compute is split into Whisper (the transcribe stage's runs), rendering (`metrics.seconds.render` of the clean stage) and the LLM.
  - The LLM part comes from `MeteredLLM`, which wraps the stage's LLM client and stores `metrics.llm` = calls, prompt/output tokens and seconds in the clean and chapters stages.
  - Local tools cost no money (D-020), so time and tokens are the cost.
- **Success metrics (`reel.metrics.summarize`)** cover the jobs created in a period (7, 30 or 90 days, or all; default 30):
  - status counts;
  - failed attempts per stage, and jobs that ended Failed per stage;
  - the median time from the file to the links (job creation → Done);
  - the same-evening rate: Done on the local date the file arrived, at or before `schedule.same_evening_cutoff`. Q-3 is not answered yet, so the default 23:59 is used;
  - the share Done without any staff event;
  - the median processing time;
  - Whisper, render and LLM hours, and LLM tokens;
  - jobs over the budget.
- **Budget (NFR-8):** `budget.max_processing_minutes` (default 240) per recording.
  - After every stage attempt, the worker adds the flag `budget_exceeded` and a `budget_exceeded` event once, when the total passes the budget.
  - The staff notifier sends an alert for that event, with the minutes.
  - The job goes on; the budget is a warning, not a stop.
- **Pages:** `/metrics` (Armenian; period filter; key numbers, compute, failures per stage, the latest recordings) and `/metrics.json` (the same summary and every job's numbers; staff login needed). The recording page has a "Ծախս և ժամանակ" section.

### D-051 — Uploading recordings through the dashboard
- **Date:** 2026-11-20 · **Source:** agent (T-807; D-035). The user asked to continue without questions, so the task's three questions were answered with these defaults; the user can change them. · **Status:** Accepted
- **Answers to the task's questions:**
  - **Size limit:** `web.upload_max_gb`, default 20 GB per file; at most 10 files per lesson. An upload is refused if less than 2 GB of disk would stay free.
  - **Who may upload:** every signed-in staff member. The name is stored with the recording.
  - **Do values entered at upload override the file name:** yes. The group (required), the lesson number and the date given in the form win. Only values left empty are filled from the file names and metadata at import.
- **Protocol:** a small JSON API used by `static/upload.js`, plain JavaScript and Reel's only script. The CSP now allows `script-src 'self'` (still no inline scripts).
  - Each file is sent in 8 MB pieces with `PUT …/files/<n>?offset=`.
  - The size of the file on disk is how far it got. A piece must start exactly there (otherwise 409 with the right offset), so a resent piece is never written twice, and a dropped connection keeps what arrived.
  - The browser retries with backoff (up to 30 s) and continues from the server's offset.
  - If the page was closed, choosing the same files again resumes the same upload (its id is kept in the browser's localStorage).
- **Placement:** pieces are written in `data/uploads/<id>/`. On "finish", the folder moves in one rename to `data/recordings/<id>/`, and one recording and one job are created directly, starting at import.
  - The watched folder is not used, because a watched file is always a whole lesson (D-035), so the parts of one lesson would become separate jobs.
  - One file is a normal recording. Several files are its parts (`media_info["parts"]`), in the order chosen in the form: by file time first, changeable with ↑ ↓.
- **Import of parts:** the content hash covers all parts in order, so the same set of files is a duplicate. Every part is validated, and the minimum duration applies to the total. Each part's audio is extracted, then the parts are joined with each part cut or padded to exactly its probed duration, so the transcript's times match the clean stage's video timeline.
- **No schema migration was needed:** the parts live in the existing `media_info` JSON.
- **Fix:** `JobQueue.identify` (T-804) now also sets `identification.set_by_staff`, so re-running the import keeps the staff's values.

### D-052 — One process for everything, and restarts
- **Date:** 2026-11-20 · **Source:** agent (T-901; NFR-2, NFR-3, UJ-4) · **Status:** Accepted
- **`reel serve`** starts the staff notifier, the folder watcher, the workers and the dashboard in one process (`reel.runtime.Runtime`).
  - Any part can be left out with `--no-web`, `--no-watcher`, `--no-worker` or `--no-notifier`. `reel worker` and `reel watch` still exist on their own.
  - The dashboard runs in a thread, so Reel's own Ctrl+C / SIGTERM handling stops everything.
  - If a part stops by itself (e.g. the port is taken), everything stops with an error in the log.
- **Graceful stop:** the dashboard stops taking requests, and the watcher and the notifier stop. The workers ask the running stages to stop and wait `workers.stop_grace_seconds`; an interrupted attempt is given back to the queue and runs again next time.
- **After a crash:** at start, Reel ends at once the leases held by Reel processes on this computer that no longer run (`JobQueue.expire_leases_of`, the PID is checked with `OpenProcess`; nothing is signalled).
  - Their jobs are recovered like after an expired lease: the attempt counts (D-028), and the job continues from the same stage after the usual backoff, not after the lease time (5 min).
  - Finished stages are not repeated; publications are idempotent (D-048).
  - Leases of other computers still run out by themselves.
- **Notifications:** exactly one `StaffNotifier` runs per database, in `reel serve`. It starts first, so on a new database the old events are only marked as seen. The worker's Attention hook only logs. If the notifier cannot start (e.g. Slack is live without a token), the error is logged and the rest runs.
- **Terminal commands:** `reel status [job] [--status] [--limit]`, `reel rerun <job> --from <stage>`, `reel release <job> [--accept]`, `reel cancel <job>` and `reel retry <job>`. They use the same queue actions as the dashboard and are audited with `--by` (default: the Windows user name) and `--note`. `user`, `config`, `db` and `auth` already existed.

### D-053 — Integration API for the other teams
- **Date:** 2026-11-20 · **Source:** agent (T-806; FR-28, D-008). T-806 was a GPT candidate (D-031), but the user paused GPT, so Claude did it. · **Status:** Accepted
- **Endpoints:** `GET /api/v1/recordings` (filters: `status`, `group`, `updated_since`), `GET /api/v1/recordings/{id}` and `GET /api/v1/library`.
  - Paging is by id: `after` and `limit` (at most 200), with `next_after` in the answer, so a page never skips or repeats an item while new recordings arrive.
  - The contract is in `docs/integrations/reel-api.md`. New fields may be added to v1; removing or renaming one needs v2.
- **Authentication:** `Authorization: Bearer <token>`.
  - The tokens are in `REEL_API_TOKENS`, comma-separated; tokens shorter than 24 characters are ignored and logged.
  - Tokens are compared in constant time.
  - Without tokens the API answers 503 (off).
  - The API does not use dashboard sessions: no cookie, no CSRF (it is read-only), `Cache-Control: no-store`, and JSON errors also for unknown `/api/` paths.
- **Privacy:** no transcripts, no student ids or consent details (only the result, and whether staff overrode it), no file paths. Publication links appear only for real publications; dry-run previews show `dry_run: true` and `url: null`.

### D-054 — Data retention
- **Date:** 2026-11-20 · **Source:** agent (T-902; NFR-6, D-021). Q-11 (the periods) is not answered, so the config defaults are used: 30 days, 365 days and sources kept. T-902 was a GPT candidate, done by Claude while GPT is paused. · **Status:** Accepted
- **What ends when:** only jobs that ended (Done, Failed or Cancelled) are touched, counted from `finished_at` (or, for Failed, `updated_at`). Running, queued and held jobs are never touched.
  - **Working files:** after `retention.working_files_days`, the whole `data/jobs/<id>/` folder (audio, cleaned video, thumbnail, JSON files, job log). A re-run from Cleaned makes them again; a re-run from Uploaded says so.
  - **Transcripts:** after `retention.transcripts_days`, the raw and clean transcript rows. The title, description and chapters stay.
  - **Unfinished uploads:** after `working_files_days`. Only folders with Reel's `upload.json` are touched.
  - **Source recordings:** only when `delete_source_recordings = true`, only of Done jobs, after `source_recordings_days`. Only files inside a watched folder or `data/recordings` are deleted, never a file elsewhere (D-021).
  - `0` days keeps everything of that kind forever.
- **Audit:** every removal is a `cleanup` event on its job. Events are never deleted, so `job_events` stays append-only and its ids are never reused (the T-801 note; no migration needed). The dashboard's thumbnail now checks that the file exists.
- **When:** `reel serve` runs the cleanup 10 minutes after start, then every 24 hours (`--no-cleanup` turns it off). `reel cleanup [--dry-run]` runs it by hand; `--dry-run` only lists. A file that cannot be removed (e.g. open in a player) is reported and tried again next time.

### D-055 — Running Reel unattended
- **Date:** 2026-11-20 · **Source:** agent (T-903, PRD §8.3, D-009). T-903 was a GPT candidate, done by Claude while GPT is paused. · **Status:** Accepted
- **Windows autostart:** `scripts/autostart.ps1 install|start|stop|status|uninstall` registers the Task Scheduler task **Reel** for the current user.
  - The task starts at sign-in, and later if the PC was off then (`StartWhenAvailable`).
  - It runs `.venv\Scripts\pythonw.exe -m reel serve` in the project folder, so no console window opens.
  - It restarts Reel 3 times, one minute apart; it has no time limit, runs on battery and never starts a second copy.
  - It runs only while the user is signed in, so Windows stores no password, and it needs no administrator rights.
  - The guide is `docs/setup/autostart.md`.
  - Without a console, `reel` sends its terminal output to `os.devnull`; the log files still get everything.
- **No Docker image:** the optional Linux image is not provided. Reel's Google token store (Credential Manager, D-047) and the YouTube session protection (DPAPI) are Windows-only, rendering uses Quick Sync, and Docker is not used (D-009). A Linux deployment would first need a Linux secret store; that would be a new task.

### D-056 — The PC's sleep during the work
- **Date:** 2026-11-20 · **Source:** agent (found in the T-1002 trial; NFR-1, NFR-2, NFR-8) · **Status:** Accepted
- **What happened:** the laptop ran the trial on battery. At 03:35 the battery was empty and Windows put the PC to sleep (event 42, "Sleep Reason: Battery") in the middle of the Cleaned stage. It woke at 14:02 when it was plugged in.
  - The stage timeout counted the 10.5 hours of sleep as work, so the stage was stopped as "timed out after 21600 s" and run again (attempt 2).
  - The time budget saw 709 min of processing and warned.
  - On Windows, Python's `time.monotonic` (QueryPerformanceCounter) goes on during sleep.
- **Decision:**
  - `reel.power.awake_clock` uses `QueryUnbiasedInterruptTime`, which leaves out sleep and hibernation; other systems use `time.monotonic`, which already does. The worker's stage timeout follows this clock. Heartbeats still follow `time.monotonic`, so the lease is renewed as soon as the PC wakes.
  - The worker stores the time asleep as `suspended_seconds` on the stage run (when it is at least 1 s) and logs it. Processing time and the budget leave it out. The time from file to links (M-1) still includes it, because it is real waiting.
  - While a stage runs, the stage thread asks Windows not to sleep for being idle (`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`), and clears the request when the stage ends. Setting: `workers.keep_awake = true`. The PC can still sleep when idle between jobs.
  - A worker's claim does not recover the expired leases of its own running slots (`claim(keep=…)`). Before, after a sleep longer than the lease, the other slot could take the job away from a stage that was still running.
- **Not prevented, and rightly so:** Windows still sleeps on a low battery, a closed lid or the power button. The PC that runs Reel must stay plugged in (README, troubleshooting).

### D-057 — The cleaned video while publishing is local only
- **Date:** 2026-11-20 · **Source:** the user's request; the agent found the retention risk · **Status:** Accepted
- **Problem:** publishing stays dry-run (local only) until the user says otherwise. Retention removed a job's folder 30 days after the job ended, on the assumption that the video was on YouTube (D-054). In dry-run, the folder's `cleaned.mp4` is the only copy, so finished lessons would have been lost.
- **Decision:**
  - **Retention:** the working files and the source recordings of a Done job are removed only when its YouTube publication is live (status published, not dry-run, with a video id). Otherwise the cleanup keeps them, and `reel cleanup` prints them under "Kept". Old transcript rows still go after `transcripts_days`; the title, description and chapters stay. Failed and cancelled jobs are handled as before.
  - **Recording page:** when `data/jobs/<id>/cleaned.mp4` exists, the page shows a video player (`GET /recordings/<id>/video`, with Range requests for seeking) and a download link (`?download=1`, named after the title, made safe for Windows file names). Staff login is required. When the video is not on YouTube, the page says that it is only on this PC.
- **Later:** when publishing goes live, the kept dry-run jobs stay until they are re-run from Uploaded (they are then on YouTube) or removed by hand.

### D-058 — Board framing
- **Date:** 2026-11-20 · **Source:** the user's request (T-1006) · **Status:** Accepted
- **The user's rule:** as the last step of the video processing, bring the video's edges close to the board, leaving a small margin. A room may have a board, a projector screen, or a whole wall to write on. In that case Reel finds the outermost points where work is done and zooms to them.
- **Decision:** Reel does not look for a board's shape. It looks for traces of work.
  - **Where the work is:** the pictures of the board check (about 1 per second, 416 px, T-403) are reused, so there is no second decoding.
    - Every person the detector sees is masked out.
    - Every 60 s, the per-pixel median gives a still picture of the room without the people moving in it.
    - The fine detail of each still (writing, slide text) is compared with the previous one. Changed detail is work. A window where most of the picture changed (the camera moved, the lights changed) is skipped.
  - **What is left out:**
    - isolated specks;
    - small traces touching the picture's bottom edge, such as heads near the camera that the detector missed;
    - groups of traces with less than 2 % of the work, such as a laptop on a table.
  - Writing inside a kept group is never trimmed. A first version cut off the outer 1 % on each side, which cut the edge of real writing in the trial lesson.
  - **The crop:** the work box gets a margin (6 % of its size, at least 2 % of the picture), is widened to 16:9 and kept inside the picture. The zoom is at most 2× (4K to 1080p keeps full detail). A crop that would keep more than 95 % of the width is not made.
  - **Several parts from one camera** (the same picture size) get one crop around all their work, so the picture does not jump at the joins.
  - **Render:** in the same single encode, the crop comes before the scaling. On the CPU this is `crop`; on Quick Sync it is `vpp_qsv` with `cx/cy/cw/ch`. The thumbnail comes from the cleaned video, so it is framed too.
- **The user's correction (same day):** keep the logic, but the crop's minimum size must include the whole board.
  - The surface the work was on is found from the work box outwards, using its brightness taken from the unwritten parts of the work box. Rows and columns are added while the surface covers at least half of the span.
  - Gaps up to 3 % of the height and 4 % of the width are crossed, such as the rails between sliding panels. Thin poles and legs cover too little of a row to count.
  - The crop is around the union of the board and the work, so writing on the wall next to the board stays in too.
  - When the surface cannot be told from the room (a wall to write on, a white board on a white wall), it would fill 92 % or more of the picture. Then only the work box is used.
  - The margin became 3 % of the box (at least 1.5 % of the picture), because the box is now the whole board.
- **Settings:** `[cleaning.framing]` (`enabled`, `margin`, `min_margin`, `max_zoom`, `min_gain`, `window_seconds`, `change_level`, `max_changed_share`, `min_work_share`).
- **Known limits:**
  - **Fixed framing:** the crop is fixed for the whole lesson. It does not follow the teacher.
  - **Board already fills the picture:** the zoom is small. In the trial lesson, the board takes about 87 % of the picture's width and all three panels were written on. The result was 1.08×: nothing was cut, and the floor and the ceiling became a little smaller.
  - **A lecture with no writing and no slide changes** gets no crop.

### D-059 — The LLM only confirms the lesson's start and end
- **Date:** 2026-11-20 · **Source:** the user's review of the first 15 minutes of the real lesson · **Status:** Accepted
- **What happened:** the recording starts right when the teaching starts, with a recap of the previous lesson and no greeting. The local LLM (Gemma 3 4B) named an ordinary teaching line, "now let's take an example", as the lecturer's greeting. Its quote was really in that line, so it passed the quote check (D-039), and the first 2:48 of the lesson were cut as "before the lesson".
- **The user's rule:** a lesson does not always start with a phrase that announces it. A recording may start right as the teaching starts, and then nothing may be cut.
- **Decision:** the line the LLM names, for the start or for the end, must also hold one of the configured phrases (greeting, topic opener or closing, with the same fuzzy matching). Otherwise the answer is rejected: nothing is cut, and the job gets the "lesson start/end not found" flag. The LLM still chooses between phrase candidates, for example a lecturer's greeting without a topic opener after it. A teacher's own way of greeting is added to the phrase lists in `config/reel.toml`.

### D-060 — The dashboard on the local network (HTTPS)
- **Date:** 2026-11-20 · **Source:** the user ("make the HTTPS setup so staff can sign in from the network"), agent (T-1008) · **Status:** Accepted
- **Need:** staff open the dashboard from their own computers on the school's network. Passwords and session cookies must not cross the network in clear text. There is no public domain, and everything must be free (D-009).
- **Decision:** Reel serves HTTPS itself (uvicorn's TLS), with a certificate from its own small CA.
  - `reel cert` makes the CA once (10 years) and a server certificate (397 days, the browsers' limit) for this computer's name, `<name>.local`, `localhost`, 127.0.0.1 and its private IPv4 addresses. `--name` and `--ip` add more.
  - The CA has **critical name constraints**: it may sign only those names and the private IPv4 ranges. A stolen CA key cannot be used against any other site on the staff computers that trust it. A test proves that OpenSSL refuses a certificate it signs for another name (`X509_V_ERR_PERMITTED_VIOLATION`).
  - Both keys are EC P-256, stored encrypted (PKCS#8) in `data/tls/`, git-ignored. Their password is random and kept in the secret store (Windows Credential Manager, D-047).
  - A renewal keeps the CA, so staff computers trust it only once. A new CA is made only with `--new-ca`, when the computer's name is not covered any more, or when the CA ends within 30 days.
  - `[web] tls = true` turns it on; it needs an `https://` `base_url`, so the session cookie gets `Secure` (D-049). `reel serve` checks the files and the key password before it starts, and logs a warning 30 days before the certificate ends.
  - `scripts/network.ps1 allow` (administrator) opens the port in Windows Firewall for **private** networks only.
- **Rejected:**
  - A self-signed certificate without a CA: every browser warns, and staff learn to click through warnings.
  - A reverse proxy (Caddy, nginx): another program to download, start and keep alive, for the same result on a small network.
  - A public certificate (Let's Encrypt): needs a public domain and DNS, which the school does not have for this computer.
  - HSTS: if HTTPS were turned off later, browsers would refuse the plain address for a long time.
- **Guide:** `docs/setup/network.md` (each staff computer installs `reel-ca.crt` once, after checking its thumbprint).

### D-061 — Typed groups are added to groups.toml; no transcript on the recording page
- **Date:** 2026-11-20 · **Source:** the user, after asking why an uploaded lesson did not reach YouTube · **Status:** Accepted
- **What happened:** a lesson was uploaded for the group "AI 2.6". The upload page accepts any typed name (the form fix of the same day), but `groups.toml` had only "Python 12". So the job stopped at Chapters with "Set the group of this recording", although a group was set.
- **The user's rule:** "add any group as soon as I type it".
- **Decision:**
  - When staff type a group in the upload form or in "set the group" on a recording, and neither a name nor an alias in `config/groups.toml` matches it, Reel appends `[groups."<name>"]` with empty IDs and a comment naming who added it and when (`add_group`). This happens only after the rest of the form is valid.
  - The file's text is kept as it is (comments, order). The new text is checked with the same validation before it is written, and the file is replaced in one step. A lock makes two uploads with one new group add it once. If the file cannot be changed, the typed name is still used and the error is logged.
  - The empty IDs work while the integrations run in dry-run. Before they go live, the group's YouTube playlist, Slack channel and Classroom course are filled in `groups.toml`.
  - A job whose group is set but not configured (for example, from an older upload) now says so: `The group "<name>" is not in config/groups.toml: …` (`missing_group_message`, in Chapters, Consent and Upload).
- **Also:**
  - `reel auth status` lists each Google service's permissions. A permission unticked on Google's consent screen is shown as MISSING, with a warning. That day, only `youtube.upload` had been granted: playlists would have failed.
  - The user asked to remove the "Տառադարձում (մաքրված տեսանյութի ժամանակներով)" section from the recording page. The transcript itself stays in the database and the job folder, because chapters, the title and the description are made from it.

### D-062 — English programming terms stay English in the transcript
- **Date:** 2026-11-21 · **Source:** the user ("English terms are written in Armenian letters and wrong; fix them so they are right; only the terms are not translated"), agent (T-1010) · **Status:** Accepted
- **What happened:** Whisper, told that the speech is Armenian, writes the English words a lecturer uses in Armenian letters, and differently each time: "hash table" came out as "հեշտեբլ", "հեշտեպլ" and "հեշ թե բլ" in one lesson. The mistakes went on into chapter titles and the description.
- **The user's rule:** programming terms stay English, in Latin letters and spelled right; the Armenian text around them stays Armenian.
- **Trial** (4 minutes of a real lesson, local, `data/trial/terms/`):

  | How the prompt was given | Words kept (of ~295) | Terms |
  |---|---|---|
  | none | 294 | Armenian letters |
  | first window only (`initial_prompt`) | 299 | Armenian letters, except a few at the start |
  | every window (`hotwords`), long | 220–246 | more in Latin letters |
  | every window, one short sentence | 260 | a "hash-fu, hash-fu, …" loop |

  The prompt shares the 448-token budget of each 30-second window with the speech, so in every window it cut the speech short. Losing a tenth to a quarter of a lesson is worse than misspelled terms.
- **Decision:**
  - Whisper gets the prompt (an Armenian sentence with terms in Latin letters, the group's vocabulary, then `[stt] initial_prompt`) only in the first window. `[stt] hotwords = true` would send it to every window; it is off by default.
  - After transcription, `TermFixer` (`reel.stt.terms`) replaces the Armenian-letter spellings with the English term and keeps the Armenian ending after a hyphen: "դիկշների" → "dictionary-ների", "հեշվունկթյայից" → "hash function-ից". On the trial clip it fixed 40 terms and changed no Armenian word.
  - Whisper mixes up letters that sound alike (պ/բ/փ, տ/դ/թ, կ/գ/ք, …). Spellings of five letters or more are compared with those letters folded together; shorter ones must match exactly. A folded form that two terms share counts only when it is exact.
  - The spellings come from real lessons. `[stt.terms]` adds spellings (`"call stack" = ["կոլ ստեկ"]`) or drops a built-in term (`object = []`). `[stt] fix_terms = false` turns the fixer off.
  - The job's transcription details list what was changed (`term_fixes`), so a wrong fix can be found and its spelling dropped.
  - The chapter-title prompt asks the LLM to write the terms in Latin letters.
- **Rejected:**
  - A bigger or English Whisper model: slower on this PC (T-303), and it would translate the Armenian speech.
  - Asking the LLM to fix the whole transcript: slow for a 2-hour lesson, and it may change the lecturer's words.
- **Limits:** a spelling Whisper has not produced before stays in Armenian letters until it is added to the list (`[stt.terms]` or `TERMS`).
