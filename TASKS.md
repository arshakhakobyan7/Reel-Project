# Reel — Development Plan

Source documents: `docs/PRD_English.pdf` (PRD v0.1, 18 Nov 2026), `docs/PRD_Armenian.pdf`, brief `docs/brief_reel.png`.
Decisions (`D-nnn`) are in `DECISIONS.md`. The development history and current status are in `PROGRESS.md` (primary source of truth).

- **Status values:** `TODO` · `IN PROGRESS` · `DONE` · `PARTIAL` (what remains is written in PROGRESS.md) · `BLOCKED` (reason given)
- **Priority:** Must / Should / Could — same meaning as PRD §0 (MVP / fast follow-up / later phase).
- **Working rules (user, D-009, D-010):** everything used must be free; one task at a time — after each task, report to the user and wait for approval before starting the next one.

## Pipeline being built

```
Imported → Transcribed → Cleaned → Chapters → Consent → Uploaded → Posting links → Done
side states: On hold · Failed · Cancelled
```

The PRD lists `Cleaned` before `Transcribed`. Here the transcript is made first, because the lesson start and end are found from what the lecturer says (D-005, D-012).

## Phases

| Phase | Goal | Tasks |
|---|---|---|
| 0 | Planning, environment, configuration, logging | T-000 … T-004 |
| 1 | Database, persistent job queue, workers | T-101 … T-103 |
| 2 | Ingestion: media toolkit, folder watcher, identification, import | T-201 … T-204 |
| 3 | Transcription (local Whisper) and local LLM (Ollama) | T-301 … T-304 |
| 4 | Cleaning: silences, lesson start/end, break, cut list, render, loudness | T-401 … T-406 |
| 5 | Chapters, title, description, thumbnail | T-501 … T-504 |
| 6 | Consent check | T-601 … T-602 |
| 7 | Publishing: YouTube, Slack, Classroom, Academy Library | T-701 … T-706 |
| 8 | Operations: notifications, dashboard, metrics, integration API | T-801 … T-806 |
| 9 | Runtime, recovery, retention, packaging | T-901 … T-903 |
| 10 | End-to-end verification and documentation | T-1001 … T-1017 |

## Task summary

| ID | Task | Priority | Depends on | Status |
|---|---|---|---|---|
| T-000 | PRD analysis and development plan | Must | — | DONE |
| T-001 | Python environment and project skeleton | Must | T-000 | DONE |
| T-002 | FFmpeg (project-local) | Must | T-001 | DONE |
| T-003 | Configuration system | Must | T-001 | DONE |
| T-004 | Structured logging | Must | T-003 | DONE |
| T-101 | Database layer and models | Must | T-003 | DONE |
| T-102 | Job state machine and persistent queue | Must | T-101 | DONE |
| T-103 | Worker runtime | Must | T-102, T-004 | DONE |
| T-201 | Media toolkit and synthetic test media | Must | T-002, T-004 | DONE |
| T-202 | Folder watcher | Must | T-103, T-201 | DONE |
| T-203 | Group and lesson identification | Must | T-003 | DONE |
| T-204 | Import stage | Must | T-202, T-203 | DONE |
| T-301 | Transcript model and STT interface | Must | T-101 | DONE |
| T-302 | Local Whisper transcriber and Transcribed stage | Must | T-204, T-301 | DONE |
| T-303 | STT benchmark and default model | Must | T-302 | DONE |
| T-304 | Local LLM (Ollama) client | Must | T-003 | DONE |
| T-401 | Silence and static-video detection | Must | T-201 | DONE (GPT, reviewed) |
| T-402 | Lesson start/end detection | Must | T-301, T-304 | DONE |
| T-403 | Break detection (empty board, D-040) | Must | T-402 | DONE |
| T-404 | Cut list and timeline mapping | Must | T-401, T-402, T-403 | DONE |
| T-405 | Renderer and loudness normalisation | Must | T-404 | DONE |
| T-406 | Cleaned stage and transcript remap | Must | T-405, T-302 | DONE |
| T-501 | Chapter generation | Must | T-406, T-304 | DONE |
| T-502 | Title and description | Must | T-501 | DONE (GPT deterministic part, reviewed; topic summary in T-501) |
| T-503 | Thumbnail | Should | T-406 | DONE (GPT, reviewed) |
| T-504 | Chapters stage | Must | T-502, T-503 | DONE |
| T-601 | Consent provider (Core Engine port) | Must | T-003 | DONE (GPT, reviewed) |
| T-602 | Consent stage, holds and audit log | Must | T-601, T-103 | DONE |
| T-701 | Secrets store and Google OAuth | Must | T-003 | DONE |
| T-702 | YouTube publisher | Must | T-701 | DONE (GPT, reviewed; OAuth tokens come from T-701) |
| T-703 | Slack publisher | Must | T-003 | DONE (GPT, reviewed) |
| T-704 | Google Classroom publisher | Must | T-701 | DONE (GPT, reviewed; OAuth tokens come from T-701) |
| T-705 | Academy Library port | Should | T-101 | DONE (GPT, reviewed) |
| T-706 | Uploaded and Posting-links stages | Must | T-602, T-702, T-703, T-704, T-705 | DONE |
| T-801 | Staff notifications | Must | T-703 | DONE (GPT, reviewed) |
| T-802 | Web foundation and staff authentication | Must | T-101 | DONE |
| T-803 | Recordings dashboard | Must | T-802 | DONE |
| T-804 | Recording detail and staff actions | Must | T-803, T-602 | DONE |
| T-805 | Metrics, cost and budgets | Must | T-803 | DONE |
| T-806 | Integration API for other teams | Should | T-802 | DONE |
| T-807 | Upload recordings (one or several parts of a lesson) through the dashboard | Must | T-802, T-204 | DONE |
| T-901 | All-in-one runtime and CLI | Must | T-202, T-802 | DONE |
| T-902 | Data retention | Must | T-901 | DONE |
| T-903 | Deployment packaging | Could | T-901 | DONE (the user runs the one-time install) |
| T-1001 | End-to-end tests on synthetic recordings | Must | T-706, T-804, T-901 | DONE |
| T-1002 | Real recording trial | Must | T-1001, T-303 | IN REVIEW (trial done; waiting for the user's sign-off) |
| T-1003 | Live publishing check | Must | T-1001 | TODO (needs free test accounts / credentials) |
| T-1004 | Documentation | Must | T-1001 | DONE |
| T-1005 | Final verification | Must | all | DONE (to be refreshed after T-1002 and T-1003) |
| T-1006 | Board framing (user request) | Must | T-403, T-405 | DONE (the user reviews it on a real lesson) |
| T-1007 | Dashboard design: dark brand colour system (user request) | Should | T-802 … T-807 | DONE (GPT, reviewed; the user checks it in the browser) |
| T-1008 | The dashboard on the local network over HTTPS (user request, D-060) | Should | T-802, T-901 | DONE (the user opens the firewall and installs the CA on staff computers) |
| T-1009 | Why the video did not reach YouTube: typed groups added to groups.toml, clear hold message, permission check; transcript removed from the page (user request, D-061) | Must | T-807, T-504 | DONE |
| T-1010 | English programming terms stay English in the transcript (Latin letters, right spelling): term fixer after Whisper (user request, D-062) | Must | T-302 | DONE |
| T-1011 | Board framing on a light wall: the board's frame lines; legs under a desk left out (user request, D-063) | Must | T-1006 | DONE |
| T-1012 | Unclear speech: hold for staff; publish as it is, with staff chapters, or cancel (user request, D-064) | Must | T-504, T-804 | DONE |
| T-1013 | Accounts: registration, users see their own uploads (only processed unless allowed to publish), admin account pages (user request, D-065) | Must | T-802, T-807 | DONE |
| T-1014 | A user's processed video and uploaded files are deleted 30 days after the job ended; the page tells the date (user request, D-066) | Must | T-902, T-1013 | DONE |
| T-1015 | Duplicates per account: a user's upload is not cancelled because another account has the same file; Armenian reason on the page (user report, D-067) | Must | T-204, T-1013 | DONE |
| T-1016 | "My page" (`/profile`): every account checks and updates its details and password; sign-in by email; no cost panel for users (user request, D-068) | Must | T-1013 | DONE (GPT, reviewed) |
| T-1017 | Rename the «Գլուխներ» section of the recording page to «Հատվածավորում» (only that section; user request) | Should | T-1016 | GIVEN TO GPT (handoff/T-1017.md, after T-1016) |

---

## Task details

### Phase 0 — Planning and environment

#### T-000 — PRD analysis and development plan
- **PRD:** all · **Priority:** Must · **Depends on:** — · **Status:** DONE
- **Description:** Inspect `ReelProject` and the machine; analyse the PRD and the brief; ask the user the blocking questions; record the answers; write TASKS.md, PROGRESS.md, DECISIONS.md, ARCHITECTURE.md; copy the PRD and brief into `docs/`.
- **Expected result:** plan documents in the project root, ready for the user's review.
- **Acceptance criteria:** every FR, NFR, UJ and C item of the PRD maps to at least one task (see the traceability matrix at the end); user answers recorded as D-001 … D-010.

#### T-001 — Python environment and project skeleton
- **PRD:** NFR-10 · **Priority:** Must · **Depends on:** T-000 · **Status:** DONE
- **Description:** Install Python 3.14 with the free Python install manager (D-011); create `.venv` inside ReelProject; `pyproject.toml` (src layout, setuptools); package `src/reel` with `__version__`; CLI entry point `reel` with `--version`; folders from ARCHITECTURE.md §11; `.gitignore`; dev tools pytest and ruff; README stub.
- **Expected result:** a runnable, testable empty application.
- **Acceptance criteria:** (1) `.venv\Scripts\python --version` reports 3.14.x; (2) `reel --version` prints the version; (3) `pytest` passes a smoke test; (4) `ruff check` passes; (5) nothing is created outside ReelProject except the Python 3.14 runtime.

#### T-002 — FFmpeg (project-local)
- **PRD:** C-5, FR-6, FR-10, FR-11 · **Priority:** Must · **Depends on:** T-001 · **Status:** DONE
- **Description:** `scripts/setup_ffmpeg.py` downloads the free Gyan.dev "essentials" Windows build (pinned 9.0.1, from the official GitHub mirror), verifies its SHA-256 checksum and extracts `ffmpeg`/`ffprobe` into `tools/ffmpeg/` (D-015). The app reads the paths from config (fallback: PATH) — wired in T-003.
- **Expected result:** FFmpeg available to the app without a system-wide install.
- **Acceptance criteria:** (1) the script fails on a checksum mismatch; (2) `ffmpeg -version` and `ffprobe -version` run from `tools/ffmpeg`; (3) required filters and encoders are present: silencedetect, freezedetect, loudnorm, thumbnail, trim/atrim, concat, afade, libx264, aac; (4) re-running the script does nothing when FFmpeg is already installed.

#### T-003 — Configuration system
- **PRD:** FR-34, NFR-9, NFR-5 · **Priority:** Must · **Depends on:** T-001 · **Status:** DONE
- **Description:** Settings in `config/reel.toml` (folders, thresholds, templates, provider options, phrase lists); group mapping in `config/groups.toml` (group → YouTube playlist, Slack channel, Classroom course, Core Engine group id, STT vocabulary, folder aliases); LLM prompts in `config/prompts/*.txt`; secrets only from environment / `.env`, never in TOML. Validation with pydantic and readable errors. Config is re-read at the start of every stage, so changes need no code deploy. Commands `reel config check`, `reel config init`, `reel config show`. Committed examples: `config/*.example.*`, `.env.example`. Conventions: D-025.
- **Expected result:** one typed settings object used by all modules.
- **Acceptance criteria:** unit tests: the example config loads; invalid values give errors that name the key; environment overrides work; the schema has no secret fields. `reel config check` exits 0 for the example config and non-zero for a broken one.

#### T-004 — Structured logging
- **PRD:** NFR-7 · **Priority:** Must · **Depends on:** T-003 · **Status:** DONE
- **Description:** JSON-lines log `logs/reel-<role>.jsonl` (one file per process role, rotated by size; D-026) with time, level, component, job id, stage, message and extra fields; readable console output; per-job log `data/jobs/<id>/job.log`; context helper that attaches job id and stage.
- **Expected result:** every later module logs through one setup.
- **Acceptance criteria:** unit tests check JSON fields, job-context propagation and redaction of secret values; rotation is configured.

### Phase 1 — Data model and job queue

#### T-101 — Database layer and models
- **PRD:** §8.2, FR-5, FR-9, FR-14, FR-22 · **Priority:** Must · **Depends on:** T-003 · **Status:** DONE
- **Description:** SQLAlchemy 2.x (2.1, the current stable series) on SQLite (WAL, foreign keys, busy timeout). Models: Recording, Job, StageRun, JobEvent, CutList, Transcript, VideoMetadata, ConsentCheck, PublicationTarget, LibraryEntry, SeenFile, StaffUser. Command `reel db init` (plus `reel db status`, `reel db upgrade`); schema version table. Conventions: D-027.
- **Expected result:** persistent storage for all PRD entities and the queue.
- **Acceptance criteria:** unit tests create the schema in a temporary database, insert and read every entity, enforce a unique content hash per Recording and one Job per Recording, and confirm WAL mode.

#### T-102 — Job state machine and persistent queue
- **PRD:** FR-5, FR-31, FR-32, NFR-2, UJ-4 · **Priority:** Must · **Depends on:** T-101 · **Status:** DONE
- **Description:** Stage order (D-012) and statuses `queued`, `running`, `on_hold`, `failed`, `done`, `cancelled`. Operations: enqueue; atomic claim with a lease; heartbeat; complete (advance or Done); fail (retryable → backoff `base × factor^(attempt−1)`, capped by `workers.backoff_max_seconds`, up to a configurable number of attempts, then Failed; non-retryable → Failed at once); hold(reason) / release(by) / cancel(by); re-run from a stage (by); recovery of expired leases (a crash counts as one attempt). Every transition writes a JobEvent and a StageRun. Rules: D-028.
- **Expected result:** jobs survive restarts and never run twice at the same time.
- **Acceptance criteria:** unit tests for every transition, and invalid transitions are rejected; two concurrent claimers never get the same job; a simulated crash (expired lease) re-queues the job, which later completes; backoff times are correct; history is preserved.

#### T-103 — Worker runtime
- **PRD:** NFR-3, NFR-10, FR-31 · **Priority:** Must · **Depends on:** T-102, T-004 · **Status:** DONE
- **Description:** Stage interface (`name`, `heavy`, `run(ctx)`), stage registry, worker threads (configurable count), a limit on parallel heavy stages (Whisper, render, LLM) enforced through the database, heartbeat during long stages, per-stage timeout, graceful shutdown. Command `reel worker` (option `--until-idle`). Rules: D-029.
- **Expected result:** stages run in order with retries and holds handled in one place.
- **Acceptance criteria:** tests with dummy stages: a job passes all stages in order; a failing stage is retried and then fails, and the notification hook is called; a stage that raises Hold puts the job On hold; with 3 jobs and limit 1, only one heavy stage runs at a time; stopping the worker releases its leases.

### Phase 2 — Ingestion

#### T-201 — Media toolkit and synthetic test media
- **PRD:** FR-2, C-5, R-8 · **Priority:** Must · **Depends on:** T-002, T-004 · **Status:** DONE (D-030)
- **Description:** Wrappers: `probe()` (duration, streams, codecs, resolution, fps, creation time); `validate()` (video and audio present, decodable, minimum duration, not truncated); `extract_audio()` (16 kHz mono WAV); a runner with progress parsing and cancellation. `scripts/make_test_media.py` generates short synthetic "lessons" (test picture, tone/noise as speech stand-in, silences, static and changing picture).
- **Expected result:** reliable FFmpeg building blocks plus test media for all later tests.
- **Acceptance criteria:** tests on generated media: probe values match; a corrupt or truncated file fails validation with a reason; extracted audio is 16 kHz mono with the right duration (±0.05 s).

#### T-202 — Folder watcher
- **PRD:** FR-1, FR-2, C-4, R-8 · **Priority:** Must · **Depends on:** T-103, T-201 · **Status:** DONE (command `reel watch [--once]`)
- **Description:** Polling watcher (works for local and synced cloud folders, D-024): scans configured folders recursively, filters extensions, ignores temporary/hidden/partial files, waits until size and modification time have not changed for `stable_seconds`, then records the file (SeenFile) and enqueues exactly one import job; survives restarts.
- **Expected result:** new recordings become jobs automatically.
- **Acceptance criteria:** tests with temporary folders: a growing file is not enqueued; a stable file is enqueued once; a restart does not enqueue it again; unsupported extensions are ignored; a file deleted before it becomes stable is ignored.

#### T-203 — Group and lesson identification
- **PRD:** FR-3, A-4, Q-5 · **Priority:** Must · **Depends on:** T-003 · **Status:** DONE
- **Description:** Convention `<watched folder>/<group folder>/<file>` (D-018): the group comes from the folder name (case-insensitive, aliases in groups.toml); the lesson number comes from configurable patterns (`Lecture 14`, `L14`, `Դաս 14`); the date comes from the file name (`YYYY-MM-DD`), else the media creation time, else the file time. An unknown group or a missing number sets an identification flag. The job continues through the heavy stages and holds before Chapters until staff fix it.
- **Expected result:** every recording gets a group, lesson number and date, or a clear flag.
- **Acceptance criteria:** table-driven tests with English and Armenian names; an unknown group or a missing number produces a flag, never an exception.

#### T-204 — Import stage
- **PRD:** FR-2, FR-4, FR-5, FR-12 · **Priority:** Must · **Depends on:** T-202, T-203 · **Status:** DONE
- **Description:** Stage `import`: streamed SHA-256 and duplicate check (same hash → job cancelled as a duplicate, with a reference to the original); validation; audio extraction to `data/jobs/<id>/audio.wav`; original duration and media info saved; identification applied.
- **Expected result:** each new file is validated once, duplicates are never processed twice.
- **Acceptance criteria:** tests: the same file under another name or path is not processed again; an invalid file → job Failed with a clear error and a notification hook call; a valid file → job moves to `transcribe` with its artifacts present.

### Phase 3 — Transcription and local LLM

#### T-301 — Transcript model and STT interface
- **PRD:** FR-13, FR-14 · **Priority:** Must · **Depends on:** T-101 · **Status:** DONE
- **Description:** Transcript structures (segments with start, end, text and words), JSON serialisation, database storage (raw and clean versions), `Transcriber` interface, `FakeTranscriber` with scripted Armenian segments for tests.
- **Expected result:** a common transcript format for every later stage.
- **Acceptance criteria:** round-trip tests; the fake transcriber is usable by later stage tests.

#### T-302 — Local Whisper transcriber and Transcribed stage
- **PRD:** FR-13, FR-14, R-4, Q-2 · **Priority:** Must · **Depends on:** T-204, T-301 · **Status:** DONE
- **Description:** faster-whisper (free, MIT) on CPU with int8; language fixed to Armenian `hy` (D-002); Silero VAD; word timestamps; per-group vocabulary (code terms) as the initial prompt; settings that reduce hallucinated text; models stored in `models/whisper`; progress reported through the heartbeat. Stage `transcribe` stores the transcript on the raw timeline.
- **Expected result:** a timestamped Armenian transcript of each recording, made locally.
- **Acceptance criteria:** a smoke test with the `tiny` model runs end to end on a short generated file; the stage stores transcript JSON and a database row; progress events are recorded; the model files are inside ReelProject.

#### T-303 — STT benchmark and default model
- **PRD:** NFR-1, M-2, R-4, R-6 · **Priority:** Must · **Depends on:** T-302 · **Status:** DONE
- **Description:** Measure speed (× real time) and Armenian quality of `small`, `medium`, `large-v3-turbo` and `large-v3` on this PC; choose the default model; estimate the total processing time for a 2 h 41 min recording.
- **Expected result:** a measured default model and a realistic time estimate.
- **Acceptance criteria:** a benchmark table in PROGRESS.md; the default model is set in the example config; the risk to M-2 is documented.

#### T-304 — Local LLM (Ollama) client
- **PRD:** FR-19, Q-7, NFR-8 · **Priority:** Must · **Depends on:** T-003 · **Status:** DONE
- **Description:** Install Ollama (free, MIT) after confirming the install and model location with the user; choose an open-weights model that handles Armenian (candidates: Qwen3, Gemma 3) with a small Armenian test; `LLMClient` interface; Ollama HTTP client with JSON-schema structured output, timeouts, retries, and token and time accounting; `FakeLLM` for tests.
- **Expected result:** a local, free LLM usable by the cleaning and chapter stages.
- **Acceptance criteria:** unit tests with mocked HTTP (success; invalid JSON; timeout → retry → error); a live smoke test returns schema-valid JSON for an Armenian prompt; the model choice is recorded in DECISIONS.md.

### Phase 4 — Cleaning

#### T-401 — Silence and static-video detection
- **PRD:** FR-6, R-1 · **Priority:** Must · **Depends on:** T-201 · **Status:** DONE
- **Description:** FFmpeg `silencedetect` (configurable level and minimum duration) plus `freezedetect` on a fast, low-resolution pass. A silence is removable only where the picture is also static (D-014, configurable), so quiet live coding is kept. A configurable amount of silence is left at each side.
- **Expected result:** a list of removable silences with reasons.
- **Acceptance criteria:** synthetic tests: a long silence over a static picture is removable; a long silence over a changing picture is kept; a short silence is kept; thresholds come from config.

#### T-402 — Lesson start/end detection
- **PRD:** FR-7, R-1 · **Priority:** Must · **Depends on:** T-301, T-304 · **Status:** DONE
- **Description:** Per D-005: configurable Armenian and English phrase lists. Start = greetings ("բարև ձեզ", "բարի երեկո", …) and topic openers ("այսօր կխոսենք", "մեր այսօրվա թեման", …). End = closings ("այսօրվա համար այսքանը", "շնորհակալություն", "կհանդիպենք", …). Armenian-aware normalisation (case, punctuation, և/եւ); fuzzy matching tolerant to STT errors; search windows (first/last N minutes); anchoring (a greeting shortly before a topic opener). The LLM judges "similar expressions" that are not in the lists. With no confident match: no cut, and a flag.
- **Expected result:** the lesson start and end on the raw timeline, with evidence.
- **Acceptance criteria:** tests on scripted transcripts: correct start/end for exact, misspelled and paraphrased phrases (paraphrase via FakeLLM); a student's "բարև" during chatter does not beat the lecturer's opening when a topic opener follows; no phrases → no cut and a flag.

#### T-403 — Break detection (empty board, D-040)
- **PRD:** FR-8, Q-14 · **Priority:** Must · **Depends on:** T-402 · **Status:** DONE
- **Description:** The user's rule D-040 (replaces proposal D-013): when nobody is at the board for more than 10 s, the stretch is shortened to 5 s, whether or not someone is heard. About one picture per second is checked by a local person detector (YOLOX-tiny ONNX, Apache-2.0); keyframes are decoded on the GPU when they are dense enough. Module `reel.cleaning.presence` (`analyze_board`, `empty_stretches`); setup `scripts/setup_person_model.py`.
- **Expected result:** the empty stretches with the parts to cut, or nothing.
- **Acceptance criteria:** tests: an empty stretch over 10 s → shortened to exactly 5 s (half at each side); 10 s or less → kept; anybody seen → kept; a gap in the pictures is not cut across; the real model sees nobody on an empty picture; cancellation stops FFmpeg; a missing model gives the install command. Checked on the user's real lesson (D-040).

#### T-404 — Cut list and timeline mapping
- **PRD:** FR-9, FR-10, FR-12 · **Priority:** Must · **Depends on:** T-401, T-402, T-403 · **Status:** DONE
- **Description:** Merge the removals (before class, after class, break, silences) into ordered keep/remove segments with start, end and reason; padding around cuts; minimum keep/remove lengths; frame-aligned boundaries; final duration; mapping between raw and clean time in both directions; stored as a versioned CutList.
- **Expected result:** one exact description of what is kept.
- **Acceptance criteria:** unit tests incl. overlapping intervals, padding at 0 and at the end, monotonic mapping, clean→raw→clean identity, and final duration = sum of kept segments.

#### T-405 — Renderer and loudness normalisation
- **User requirement (D-037):** videos taller than 1080p (e.g. 4K) are scaled down to 1080p (`render.max_height`); smaller ones are not scaled up. Try Intel Quick Sync (free) for speed (D-036).
- **PRD:** FR-10, FR-11, NFR-4 · **Priority:** Must · **Depends on:** T-404 · **Status:** DONE
- **Description:** FFmpeg filter graph built from the cut list (trim/atrim, short audio fades, concat; graph passed via a file to avoid Windows command-line limits); two-pass `loudnorm` to the configured target (default −16 LUFS, −1.5 dBTP); libx264 and AAC with configurable preset/CRF (Intel Quick Sync optional); resolution and frame rate preserved; progress via heartbeat.
- **Expected result:** the cleaned, normalised video.
- **Acceptance criteria:** synthetic tests: output duration = cut-list duration ± 1 frame; audio and video durations equal ± 1 frame; integrated loudness within ±1 LU of the target; resolution unchanged; no clicks at joins (peak check around each cut).

#### T-406 — Cleaned stage and transcript remap
- **PRD:** FR-6 … FR-13 · **Priority:** Must · **Depends on:** T-405, T-302 · **Status:** DONE
- **Description:** Stage `clean`: detection → cut list → render → remap the raw transcript to the cleaned timeline (segments in removed ranges dropped, boundary segments clipped) → store; flags shown to staff.
- **Expected result:** cleaned video plus a transcript whose timestamps match it.
- **Acceptance criteria:** integration test on synthetic media with a fake transcript: the cleaned video exists; the cut list is saved; all clean-transcript timestamps are ordered and inside [0, final duration]; "X cut to Y" durations are recorded.

### Phase 5 — Chapters, title, description, thumbnail

#### T-501 — Chapter generation
- **PRD:** FR-15, FR-19, R-3 · **Priority:** Must · **Depends on:** T-406, T-304 · **Status:** DONE
- **Description:** Map-reduce over the cleaned transcript: windows of a few minutes → short Armenian topic labels → grouped into chapters with exact start times. Prompts live in `config/prompts`. Validation: first chapter at 0:00, ascending, inside the video, at least 3 chapters, each at least 10 s, non-empty titles, length limits. Invalid output → retry → fallback without chapters, plus a flag.
- **Expected result:** YouTube-valid chapters based only on the transcript.
- **Acceptance criteria:** FakeLLM tests for valid output, timestamps outside the video, too few chapters, and unparseable output (retry, then fallback with a flag).

#### T-502 — Title and description
- **PRD:** FR-16, FR-17, Q-13 · **Priority:** Must · **Depends on:** T-501 · **Status:** DONE (GPT, reviewed)
- **Description:** Title from a template (default `{group} · Lecture {lesson}`, configurable); a short topic description generated from the transcript (e.g. "Dictionaries, live coding", in Armenian); full description = topics + chapter list + optional footer; YouTube limits (title ≤ 100 characters, description ≤ 5000 bytes, no `<` `>`).
- **Expected result:** title and description ready for upload.
- **Acceptance criteria:** unit tests for template rendering, truncation, forbidden characters and chapter time formats (`M:SS`, `H:MM:SS`).

#### T-503 — Thumbnail
- **PRD:** FR-18, Q-13 · **Priority:** Should · **Depends on:** T-406 · **Status:** DONE (GPT, reviewed)
- **Description:** Representative frame (FFmpeg `thumbnail` filter, skipping the first minutes); 1280×720 JPEG under 2 MB with the title overlaid in a free font that covers Armenian and Latin (DejaVu Sans, bundled with its licence).
- **Expected result:** a thumbnail file per video.
- **Acceptance criteria:** tests check size, format and the 2 MB limit; Armenian text renders without missing-glyph boxes (glyph coverage check).

#### T-504 — Chapters stage
- **PRD:** FR-15 … FR-19, FR-3 · **Priority:** Must · **Depends on:** T-502, T-503 · **Status:** DONE
- **Description:** Stage `chapters`: identification gate (missing group/lesson → On hold "identification" with a notification); run T-501 … T-503; store VideoMetadata and flags.
- **Expected result:** all metadata needed for publishing.
- **Acceptance criteria:** integration test: a metadata row with title, description, chapters and thumbnail; missing identification → hold; after staff set group/lesson and release, the stage completes.

### Phase 6 — Consent

#### T-601 — Consent provider (Core Engine port)
- **PRD:** FR-20, Q-1 · **Priority:** Must · **Depends on:** T-003 · **Status:** DONE
- **Description:** `ConsentProvider` interface (D-008); proposed REST contract for the Core Engine team in `docs/integrations/core-engine-consent.md`; HTTP client implementing it; interim file-based consent registry (`config/consent.json`, not committed; example committed); local mock server for tests.
- **Expected result:** Reel can check consent today and switch to the Core Engine by configuration later.
- **Acceptance criteria:** tests: HTTP client against the mock server (granted, denied, unknown, timeout, 5xx); file provider; an unknown student status counts as opted out (D-019).

#### T-602 — Consent stage, holds and audit log
- **PRD:** FR-21, FR-22, UJ-3, C-3, A-5 · **Priority:** Must · **Depends on:** T-601, T-103 · **Status:** DONE
- **Description:** Stage `consent` before upload: any opted-out or unknown student, or an unreachable provider → On hold and a staff notification; ConsentCheck rows (time, students checked, result, released by); a staff release (recorded) continues to upload; cancel ends the job without publishing.
- **Expected result:** no video with an opted-out student is published automatically.
- **Acceptance criteria:** tests for every outcome; nothing reaches upload while on hold; audit rows are complete.

### Phase 7 — Publishing

#### T-701 — Secrets store and Google OAuth
- **PRD:** NFR-5, R-7 · **Priority:** Must · **Depends on:** T-003 · **Status:** DONE
- **Description:** Secrets from environment / `.env`, from the OS secret store via `keyring` (Windows Credential Manager, free), and a file fallback for development only. `reel auth google` runs the installed-app OAuth flow for the YouTube and Classroom scopes and stores the refresh token in the secret store; tokens refresh automatically; an expired or revoked token gives a clear error and a staff notification.
- **Expected result:** credentials handled safely and outside the code.
- **Acceptance criteria:** tests with fake backends; no secret appears in logs or config files; manual steps documented.

#### T-702 — YouTube publisher
- **PRD:** FR-23, FR-24, FR-25, C-1, C-2, R-5 · **Priority:** Must · **Depends on:** T-701 · **Status:** DONE (GPT, reviewed)
- **Description:** YouTube Data API v3 (free, quota-based): resumable upload with privacy hard-coded to `unlisted` (D-017); title, description with chapters, category, language; thumbnail; add to the group playlist (creating missing playlists only if enabled, Q-8); an idempotency marker (`reel-job-<id>`) checked before uploading; after upload, a check that YouTube did not lock the video to private (R-5) → flag and notification; replacement of the video when a re-run produced a different cleaned file; dry-run mode.
- **Expected result:** the video on YouTube, unlisted, in the right playlist, exactly once.
- **Acceptance criteria:** tests with a mocked API client: correct request bodies (privacy always unlisted); a retry after an interrupted upload does not create a duplicate; adding to the playlist is idempotent; dry-run makes no API calls.

#### T-703 — Slack publisher
- **PRD:** FR-26, Q-9 · **Priority:** Must · **Depends on:** T-003 · **Status:** DONE
- **Description:** Slack bot token (works on the free Slack plan, D-022); `chat.postMessage` to the group channel with a configurable Armenian message template; permalink saved; idempotency through message metadata (job id); the message is updated in place after a re-run; dry-run mode.
- **Expected result:** one link message per video in the group's channel.
- **Acceptance criteria:** tests with a mocked Slack client: one message per job across retries; permalink stored; update path works; dry-run makes no calls.

#### T-704 — Google Classroom publisher
- **PRD:** FR-27, A-6, Q-9 · **Priority:** Must · **Depends on:** T-701 · **Status:** DONE (GPT, reviewed)
- **Description:** Google Classroom API (free): announcement with a link in the group's course (post type configurable, Q-9); idempotency (look for an existing post with the same link); update/replace after a re-run; dry-run mode.
- **Expected result:** one Classroom post per video.
- **Acceptance criteria:** tests with a mocked API: correct body; no duplicate on retry; dry-run makes no calls.

#### T-705 — Academy Library port
- **PRD:** FR-28, Q-10 · **Priority:** Should · **Depends on:** T-101 · **Status:** DONE
- **Description:** `LibraryPublisher` interface (D-008); proposed REST contract in `docs/integrations/academy-library.md`; HTTP client; interim local library (LibraryEntry table) shown in the dashboard and exposed through the integration API until the Library team's product is ready.
- **Expected result:** each published video has a library entry now, and the real Library can take over later by configuration.
- **Acceptance criteria:** tests: one local entry per job (idempotent upsert); HTTP client against a mock; fields: title, link, group, lesson, date, chapters.

#### T-706 — Uploaded and Posting-links stages
- **PRD:** FR-23, FR-29, NFR-2, G-3 · **Priority:** Must · **Depends on:** T-602, T-702, T-703, T-704, T-705 · **Status:** DONE
- **Description:** Stage `upload` (YouTube) and stage `post` with separate state per target (Slack, Classroom, Library): a failing target is retried on its own with backoff while the others complete. The job is Done when all enabled targets succeed. When a target runs out of retries, the job is Failed with the partial publication recorded.
- **Expected result:** independent, duplicate-free distribution.
- **Acceptance criteria:** tests: one failing target does not block the others; retries never duplicate posts; Done only when all targets succeed.

### Phase 8 — Operations

#### T-801 — Staff notifications
- **PRD:** FR-33, UJ-3, UJ-4 · **Priority:** Must · **Depends on:** T-703 · **Status:** DONE
- **Description:** Notifications to a staff Slack channel (and the log) for Failed, On hold (consent, identification) and important flags, with stage, error and a dashboard link; one notification per state change; dry-run mode.
- **Expected result:** staff learn about problems without watching the dashboard.
- **Acceptance criteria:** tests: one notification per transition; the content includes stage, error and link.

#### T-802 — Web foundation and staff authentication
- **PRD:** FR-30, NFR-5 · **Priority:** Must · **Depends on:** T-101 · **Status:** DONE
- **Description:** FastAPI with server-rendered Jinja2 pages (no Node.js) and plain CSS; staff login/logout; passwords hashed with scrypt (Python standard library); signed session cookie; CSRF tokens on all forms; `reel user add/disable`. Only signed-in staff can see recordings and transcripts. The UI language (Armenian or English) will be confirmed with the user before this task.
- **Expected result:** a secure base for the dashboard.
- **Acceptance criteria:** TestClient tests: anonymous users are redirected to login; a wrong password is rejected; CSRF is enforced; sessions expire.

#### T-803 — Recordings dashboard
- **PRD:** FR-30, FR-12 · **Priority:** Must · **Depends on:** T-802 · **Status:** DONE
- **Description:** List of recordings: group · lesson, stage pills (as in the brief), status, original → final duration, timestamps, last error, flags; filters by status and group; automatic refresh.
- **Expected result:** staff see the state of every recording at a glance.
- **Acceptance criteria:** tests render the list with jobs in every status; checked manually in a browser.

#### T-804 — Recording detail and staff actions
- **PRD:** FR-30, FR-32, FR-9, UJ-3, UJ-4, UJ-5 · **Priority:** Must (re-run: Should) · **Depends on:** T-803, T-602 · **Status:** DONE
- **Description:** Detail page: stage timeline with timestamps, attempts and errors; cut-list bar (kept vs removed with reasons, as in the brief); transcript preview; chapters, title, description, thumbnail; consent checks; publication links; event history. Actions: release or cancel a hold; set group and lesson; re-run from a chosen stage; retry now. Every action is audited with the staff user.
- **Expected result:** staff can resolve holds, failures and wrong cuts from the browser.
- **Acceptance criteria:** tests for each action (state change, audit event, CSRF); checked manually in a browser.

#### T-805 — Metrics, cost and budgets
- **PRD:** NFR-7, NFR-8, M-1, M-2, M-3, M-6 · **Priority:** Must · **Depends on:** T-803 · **Status:** DONE
- **Description:** Per recording: processing time per stage, compute seconds (Whisper, LLM, render), LLM tokens. Aggregates: failures per stage, median time from file to links, same-evening rate (cutoff configurable, Q-3), share processed without manual intervention. Budget = maximum processing minutes per recording (local tools cost no money, D-020), with an alert when exceeded. Metrics page and JSON.
- **Expected result:** the PRD success metrics are measurable.
- **Acceptance criteria:** unit tests for the aggregations on seeded data; the page renders.

#### T-806 — Integration API for other teams
- **PRD:** FR-28 · **Priority:** Should · **Depends on:** T-802 · **Status:** DONE
- **Description:** Token-protected read-only JSON API (`/api/v1/recordings`, `/api/v1/recordings/{id}`, `/api/v1/library`) documented in `docs/integrations/reel-api.md`, to make the later merge with the Core Engine and Library products easier (D-008).
- **Expected result:** other teams can read Reel's data without touching its database.
- **Acceptance criteria:** tests: a token is required; responses match the documented schema.

### Phase 9 — Runtime, recovery, retention, packaging

#### T-807 — Upload recordings through the dashboard
- **Source:** user request (2026-11-19, D-035) · **Priority:** Must · **Depends on:** T-802, T-204 · **Status:** DONE
- **Multi-part lessons (D-035):** several files chosen in one upload form are the parts of one lesson (e.g. two iPhone `.MOV` files). Pressing the upload/confirm button means the lesson is complete. Parts are ordered by their recording time (staff can change the order in the form). One recording and one job are created for all parts; import hashes and validates every part and joins their audio; the renderer joins the video. This needs a schema migration (recording parts).
- **Description:** Signed-in staff upload a recording in the browser and choose its group (and optionally the lesson number and date). Large files (several GB, e.g. iPhone `.MOV`) are streamed to disk in chunks with progress and can resume after a dropped connection; the finished file is placed atomically into `<watched folder>/<group>/`, so the normal pipeline (watcher → import) takes over. Free software only.
- **Expected result:** staff can add a recording without access to the PC's folders.
- **Acceptance criteria:** tests: an upload of a multi-GB file (synthetic) is written in chunks without loading it into memory; an interrupted upload resumes; the job appears with the chosen group; unsupported types and uploads without login are refused. Questions for the user when the task starts: size limit, who may upload, whether values entered at upload override the file name.

#### T-901 — All-in-one runtime and CLI
- **Note from T-801:** run exactly one `StaffNotifier.run(stop)` per database (initialised before the producers on first setup); do not also notify staff from the worker's Attention hook.
- **PRD:** NFR-2, NFR-3, UJ-4 · **Priority:** Must · **Depends on:** T-202, T-802 · **Status:** DONE
- **Description:** `reel serve` runs the web app, watcher and workers in one process (separate commands also available); graceful shutdown; CLI commands status, rerun, release, cancel, user, config, db.
- **Expected result:** one command starts the product.
- **Acceptance criteria:** kill-and-restart test: a job in progress resumes from its stage without repeating finished stages or duplicating publications.

#### T-902 — Data retention
- **Note from T-801:** `job_events` ids must never be reused (the notifier's watermark): before any event is deleted, give `JobEvent` AUTOINCREMENT (`_NO_ID_REUSE`) with a schema migration, or keep `job_events` append-only.
- **PRD:** NFR-6, Q-11 · **Priority:** Must · **Depends on:** T-901 · **Status:** DONE
- **Description:** Configurable retention for working files (audio, cleaned video, transcripts) and an optional archive/delete for raw files. Source recordings are never deleted unless explicitly enabled (D-021). Runs daily and via `reel cleanup --dry-run`.
- **Expected result:** disk usage stays bounded.
- **Acceptance criteria:** tests with a fake clock: only expired artifacts are removed; dry-run lists without deleting.

#### T-903 — Deployment packaging
- **PRD:** §8.3 · **Priority:** Could · **Depends on:** T-901 · **Status:** DONE, except the check on this PC: registering the task was blocked for the agent, so the user runs `scripts\autostart.ps1 install` once (D-055)
- **Description:** Windows autostart instructions (Task Scheduler) and an optional Dockerfile for Linux servers built only on free software (Docker Engine or Podman, FFmpeg, Ollama).
- **Expected result:** Reel can run unattended.
- **Acceptance criteria:** autostart verified on this PC; the image builds if Docker is used.

### Phase 10 — Verification and documentation

#### T-1001 — End-to-end tests on synthetic recordings
- **PRD:** UJ-1, UJ-3, UJ-4, UJ-5, G-1 … G-5 · **Priority:** Must · **Depends on:** T-706, T-804, T-901 · **Status:** DONE
- **Description:** Automated end-to-end runs: a generated recording dropped into the inbox goes through all stages to dry-run publications; consent hold → release → Done; injected failure → retries → Failed → fix → re-run from the stage; reported wrong cut → settings change → re-run cleaning with no duplicate posts.
- **Expected result:** the main user journeys are proven automatically.
- **Acceptance criteria:** the E2E suite passes; every user journey has a test.

#### T-1002 — Real recording trial
- **PRD:** NFR-1, M-2, M-4, R-1, R-4 · **Priority:** Must · **Depends on:** T-1001, T-303 · **Status:** IN REVIEW: the trial ran on 2026-11-20 and the results table is in PROGRESS.md; waiting for the user's review of the cuts and chapters
- **Description:** Run real Armenian lesson recordings through local Whisper and Ollama with dry-run publishing; review cuts and chapters with the user; tune thresholds and phrase lists; measure timings.
- **Expected result:** settings tuned on real data.
- **Acceptance criteria:** a results table (timing, cut review, chapter review) in PROGRESS.md; the user signs off.

#### T-1003 — Live publishing check
- **PRD:** FR-23 … FR-29, R-5, R-7 · **Priority:** Must · **Depends on:** T-1001 · **Status:** TODO (needs free test accounts / credentials from the user)
- **Description:** Publish a test video unlisted to a test playlist; post to a test Slack channel and a test Classroom course; re-run to verify there are no duplicates.
- **Expected result:** the real integrations are proven.
- **Acceptance criteria:** links verified by the user; no duplicates.

#### T-1004 — Documentation
- **PRD:** NFR-10 · **Priority:** Must · **Depends on:** T-1001 · **Status:** DONE
- **Description:** README (install, configuration, running, troubleshooting); ARCHITECTURE update; integration guides for the Core Engine and Library teams; THIRD_PARTY.md listing every dependency and its free licence; CHANGELOG.
- **Expected result:** someone else can install, run and merge Reel.
- **Acceptance criteria:** a fresh setup that follows the README works on this PC; every dependency is listed with its licence.

#### T-1005 — Final verification
- **PRD:** all · **Priority:** Must · **Depends on:** all · **Status:** DONE: matrix and limitations in PROGRESS.md; to be refreshed when T-1002 is reviewed and T-1003 has accounts
- **Description:** Check every FR and NFR against the implementation and tests (matrix below), run the full test suite and the main flows, write the final status and known limitations in PROGRESS.md.
- **Expected result:** a verified MVP and an honest list of limitations.
- **Acceptance criteria:** matrix complete; all tests pass; limitations listed.

#### T-1006 — Board framing (user request, 2026-11-20)
- **PRD:** NFR-4 (output quality) · **Priority:** Must · **Depends on:** T-403, T-405 · **Status:** DONE: D-058; the user reviews the result on a real lesson
- **Description:** As the last step of the video processing, zoom the picture to where the lesson's work happens (a board, a projector screen, or a wall to write on), with a small margin.
- **Expected result:** the cleaned video shows the work area larger, and no writing is cut off.
- **Acceptance criteria:** works for the three kinds of boards (any surface where writing or slides change); people are not counted as work; tested on a real lesson; the user is satisfied.

---

## Traceability matrix (PRD → tasks)

| PRD item | Tasks |
|---|---|
| FR-1 Watch folders | T-202 |
| FR-2 Wait for complete file, validate | T-201, T-202 |
| FR-3 Identify group and lesson | T-203, T-504 |
| FR-4 No duplicates (content hash) | T-204 |
| FR-5 Background job and stage tracking | T-102, T-204 |
| FR-6 Silence detection | T-401 |
| FR-7 Chatter before/after class | T-402 |
| FR-8 Break | T-403 |
| FR-9 Cut list stored | T-404, T-804 |
| FR-10 Render with padding | T-404, T-405 |
| FR-11 Loudness normalisation | T-405 |
| FR-12 Original and final duration | T-204, T-404, T-803 |
| FR-13 Transcript timestamps match the cut video | T-302, T-406 |
| FR-14 Store transcript | T-301, T-302 |
| FR-15 Chapters (YouTube rules) | T-501 |
| FR-16 Title template | T-502 |
| FR-17 Description with chapters | T-502 |
| FR-18 Thumbnail | T-503 |
| FR-19 Transcript-only, validated LLM output, fallback | T-304, T-501 |
| FR-20 Consent status from Core Engine | T-601, T-602 |
| FR-21 Hold on opt-out / unreachable | T-602 |
| FR-22 Consent log | T-602 |
| FR-23 YouTube upload, unlisted | T-702, T-706 |
| FR-24 Group playlist | T-702 |
| FR-25 Title, description, thumbnail on the video | T-702 |
| FR-26 Slack post | T-703 |
| FR-27 Classroom post | T-704 |
| FR-28 Academy Library | T-705, T-806 |
| FR-29 Independent posting targets | T-706 |
| FR-30 Status by stage | T-803, T-804 |
| FR-31 Automatic retries with backoff | T-102, T-103 |
| FR-32 Re-run from a chosen stage | T-102, T-804 |
| FR-33 Staff notifications | T-801 |
| FR-34 Configuration outside code | T-003 |
| NFR-1 Timeliness | T-303, T-1002 |
| NFR-2 Reliability, idempotency | T-102, T-706, T-901 |
| NFR-3 Concurrency | T-103 |
| NFR-4 Output quality | T-405 |
| NFR-5 Privacy and security | T-003, T-701, T-702, T-802 |
| NFR-6 Data retention | T-902 |
| NFR-7 Observability | T-004, T-805 |
| NFR-8 Cost | T-805 |
| NFR-9 Configurability | T-003 |
| NFR-10 Maintainability | T-001, T-103, T-1004 |
| UJ-1 Happy path | T-1001 |
| UJ-2 Absent student catches up | T-501, T-702, T-703, T-1001 |
| UJ-3 Consent hold | T-602, T-804, T-1001 |
| UJ-4 Failure and recovery | T-102, T-801, T-804, T-1001 |
| UJ-5 Wrong cut reported | T-804, T-1001 |
| C-1 Unlisted only | T-702 |
| C-2 Group playlist | T-702 |
| C-3 Opted-out students off camera | T-602 |
| C-4 Starts from watched folder | T-202 |
| C-5 Named technologies | T-002, T-102, T-302, T-304, T-702 |
| M-1 … M-6 Success metrics | T-805 (M-7 needs viewing analytics — PRD Phase 3) |
