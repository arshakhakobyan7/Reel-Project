# Reel — Architecture

**Status:** design baseline written in T-000 (2026-11-18). Built so far: project skeleton and `reel` CLI entry point (T-001); project-local FFmpeg 9.0.1 with installer and capability check `reel.media.ffmpeg_check` (T-002); configuration package `reel.config` with `reel config check|init|show` (T-003, §7); structured logging `reel.logging_setup` (T-004, §6, D-026); database layer `reel.db` with `reel db init|status|upgrade` (T-101, §5, D-027); job queue and state machine `reel.queue` (T-102, §4, D-028); worker runtime and `reel worker` (T-103, §9, D-029); media toolkit `reel.media.ffmpeg` and test media (T-201, D-030); folder watcher `reel.ingest.watcher` and `reel watch` (T-202, D-024); group and lesson identification `reel.ingest.identify` (T-203, D-018); first pipeline stage `import` `reel.stages.importing` (T-204, D-032); transcript format and transcriber interface `reel.stt` (T-301, D-033); faster-whisper engine `reel.stt.whisper` and stage `transcribe` `reel.stages.transcribing` (T-302, D-034); local LLM `reel.llm` with the project's own Ollama (T-304, D-038); silence analysis `reel.cleaning.silence` (T-401); lesson start/end `reel.cleaning.boundaries` (T-402, D-039); empty-board check `reel.cleaning.presence` (T-403, D-040); cut list and time mapping `reel.cleaning.cutlist` (T-404, D-041); renderer `reel.media.render` (T-405, D-042); stage `clean` `reel.stages.cleaning` and transcript remap `reel.cleaning.remap` (T-406, D-043); chapters and topic summary `reel.metadata.chapters` (T-501, D-044); title/description rules `reel.metadata.text` (T-502); stage `chapters` `reel.stages.chapters` (T-504, D-045); stage `consent` `reel.stages.consent` with the release audit in `JobQueue.release` (T-602, D-046); secret store and Google OAuth `reel.auth`, `reel auth google|status|forget` (T-701, D-047); stages `upload` and `post` `reel.stages.publishing` (T-706, D-048), so every pipeline stage now exists; dashboard foundation `reel.web` (Armenian pages, staff login, sessions, CSRF), `reel serve`, `reel user` (T-802, D-049); recordings list `reel.web.views` (T-803); recording detail and staff actions `reel.web.detail`, `JobQueue.identify` (T-804); metrics, cost and budget `reel.metrics`, `MeteredLLM`, `/metrics` (T-805, D-050); dashboard upload `reel.ingest.upload`, `reel.web.uploads`, multi-part import (T-807, D-051); everything in one process `reel.runtime` (`reel serve`) and the job commands `reel status|rerun|release|cancel|retry` (T-901, D-052); read-only integration API `reel.web.api`, `/api/v1/…` (T-806, D-053); retention `reel.retention`, `reel cleanup`, daily in `reel serve` (T-902, D-054); Windows autostart `scripts/autostart.ps1` (T-903, D-055); documentation: README (install, configure, run, daily use, troubleshooting), THIRD_PARTY.md, CHANGELOG.md (T-1004); thumbnails `reel.media.thumbnail` (T-503); consent providers `reel.consent` (T-601); Slack `reel.publish.slack` (T-703); Academy Library port `reel.library` (T-705); staff notifications `reel.notify.staff` (T-801). This file is updated as tasks are completed; parts not built yet are marked *(planned)*.

## 1. What shapes the design

- **Same-evening publishing without manual editing (G-1)** on one machine, with free, local processing (D-003, D-004, D-009).
- **Every stage can be restarted safely (NFR-2).** All state is in SQLite. Every external side effect (upload, post) is recorded and checked before it could be repeated.
- **Consent is a hard gate before anything leaves the machine (FR-21).**
- **Clean seams for the Core Engine and Academy Library teams (D-008).** Both are reached only through small interfaces with documented contracts.

## 2. Components *(planned)*

```
                          ┌──────────────────────────── Reel (one Python app) ───────────────────────────┐
 watched folder(s) ─────► │ Watcher ──► SQLite (jobs, artifacts, audit log) ◄── Web dashboard (FastAPI) │ ◄── staff (browser)
                          │                 ▲        │                                                   │
                          │                 │        ▼                                                   │
                          │              Workers ── stages ──► FFmpeg · faster-whisper · Ollama (local)  │
                          └───────────────────┬──────────────────────────────────────────────────────────┘
                                              ├──► Core Engine — consent         (port: ConsentProvider)
                                              ├──► YouTube Data API — unlisted upload, playlist
                                              ├──► Slack — group link post, staff alerts
                                              ├──► Google Classroom — announcement with link
                                              └──► Academy Library — catalogue   (port: LibraryPublisher)
```

| Component | Responsibility |
|---|---|
| Watcher | Finds new, fully written recordings and creates one job per file. |
| Job queue (SQLite) | Persistent jobs, stages, leases, retries, holds, audit events. |
| Workers | Claim jobs and run the next stage; heavy stages are limited in parallel. |
| Stages | One module per pipeline step with clear inputs and outputs (NFR-10). |
| Media toolkit | FFmpeg/ffprobe wrappers: probe, validate, extract audio, detect, render. |
| STT | `Transcriber` interface; local faster-whisper implementation; fake for tests. |
| LLM | `LLMClient` interface; Ollama implementation; fake for tests. |
| Integrations | YouTube, Slack, Classroom clients; Consent and Library ports; dry-run mode. |
| Web dashboard | Staff login, status by stage, recording details, actions, metrics, integration API. |

## 3. Pipeline

| # | Stage (dashboard name) | What happens | Main outputs | PRD |
|---|---|---|---|---|
| 1 | **Imported** | Stable file → SHA-256 duplicate check → validation → 16 kHz mono audio → group/lesson/date identification | Recording, `audio.wav` | FR-1 … FR-5 |
| 2 | **Transcribed** | Local Whisper (Armenian, voice activity detection, word timestamps) over the raw audio | `transcript.raw.json` | FR-13, FR-14 |
| 3 | **Cleaned** | Silences (only over a static picture) + lesson start/end phrases + nobody at the board > 10 s → 5 s (D-040) → cut list → render with padding + loudness normalisation → transcript mapped to the cleaned timeline | `cutlist.json`, `cleaned.mp4`, `transcript.clean.json` | FR-6 … FR-13 |
| 4 | **Chapters** | Identification gate → chapters (LLM, validated) → title (template) → description → thumbnail | `metadata.json`, `thumbnail.jpg` | FR-15 … FR-19 |
| 5 | **Consent** | Consent of every student in the group → continue, or hold | ConsentCheck | FR-20 … FR-22 |
| 6 | **Uploaded** | YouTube resumable upload (unlisted), metadata, thumbnail, group playlist | Publication (YouTube) | FR-23 … FR-25 |
| 7 | **Posting links** | Slack, Classroom and Library as independent targets | Publication (per target) | FR-26 … FR-29 |

The PRD order is `Cleaned → Transcribed`. Reel transcribes first because lesson boundaries are found from speech (D-005, D-012).

## 4. Job lifecycle *(built in T-102: `reel.queue`; rules in D-028)*

```
            claim (lease)             stage OK, more stages
  queued ───────────────► running ──────────────────────────► queued (next stage)
    ▲  ▲                    │  │ │                                   …
    │  │  retryable error,  │  │ └── last stage OK ──────────────► done
    │  └── attempts left ───┘  │
    │      (backoff)           ├── fatal error / no attempts left ─► failed ── re-run from stage ─► queued
    │                          └── hold(reason) ──► on_hold ── release (staff) ─► queued
    │                                                  └────── cancel (staff) ──► cancelled
    ├── lease expired (worker crashed) — counts as an attempt
    └── interrupt (worker stopping) — not counted
```

- **Leases:** `JobQueue.claim()` leases the oldest due job to one worker inside one `BEGIN IMMEDIATE` transaction; the worker renews the lease with `heartbeat()` during long stages. A job whose lease expires is given back (counting as an attempt) by the next `claim()` or `recover_expired()`. Worker calls carry their `Claim`; a stale one (cancelled job, expired lease, older attempt) gets `LeaseLost` and changes nothing.
- **Retries (FR-31):** delay = `base × factor^(attempt − 1)`, capped by `workers.backoff_max_seconds`; attempts and timings are configurable (`[workers]`). Fatal errors fail at once. Staff can skip the wait (`retry_now`).
- **Holds (UJ-3):** a stage calls `hold(reason, message)`. Staff `release()` it to run the held stage again, or with `advance=True` to accept the stage and continue (consent).
- **Re-run (FR-32):** staff choose a stage the job has already reached; finished stages before it are not repeated; external effects are updated in place rather than duplicated (T-706).
- **Heavy stages:** `claim(heavy_stages=..., heavy_limit=...)` counts running heavy stages in the same transaction, so the limit holds across processes (used by T-103).
- **Audit:** every transition writes a `JobEvent` (who, when, what) and is logged with the job id; every stage attempt has a `StageRun` (attempt, worker, start/end, outcome, error, metrics).

## 5. Data model *(built in T-101; conventions in D-027)*

SQLite file `data/reel.db` (setting `paths.database_file`), SQLAlchemy ORM, schema version 1.

| Table | Main fields | PRD §8.2 |
|---|---|---|
| `recordings` | source path, size, file time, content hash (unique once known), duplicate of, group, lesson number, recording date, identification sources, original duration, media info, detected at | Recording |
| `jobs` | recording (unique: one job per recording), stage, status, attempts, next attempt time, lease owner/expiry, hold reason/message, last error, flags, started/finished, row version | Job |
| `stage_runs` | job, stage, attempt, status, worker, start/end, error, compute metrics | Job (stage timestamps, errors) |
| `job_events` | job, time, kind, actor kind and name, stage, message, data | audit |
| `cut_lists` | job, version, original/final duration, segments (start, end, keep/remove, reason), parameters | CutList |
| `transcripts` | job, kind (raw/clean, unique per job), language, text, segments, model, cut list used | Transcript |
| `video_metadata` | job (unique), title, summary, description, chapters, thumbnail path, generator info | VideoMetadata |
| `consent_checks` | job, time, provider, Core Engine group, students, result, message, released by/at, note | ConsentCheck |
| `publication_targets` | job + target (unique), status, dry run, external id, URL, details (e.g. playlist id), attempts, error, published at | Publication |
| `library_entries` | job (unique), title, video URL, group, lesson, date, chapters | — interim Academy Library (D-008) |
| `seen_files` | path (unique), size, file time, first seen, stable since, last checked, state, recording | watcher state |
| `staff_users` | username (unique, canonical form), display name, password hash, active, last login | dashboard auth |
| `schema_versions`, `app_meta` | applied schema versions; database `instance_id`, creation time | — |

The PRD's single Publication entity is stored as one row per target (FR-29); `Job.publication_summary()` returns the PRD fields. Deleting a recording or job deletes its rows in the other tables in the database itself (`ON DELETE CASCADE`).

| Module | Role |
|---|---|
| `reel.db.base` | declarative base, UTC datetime type, JSON and enum storage |
| `reel.db.enums` | stages (order, dashboard names), job/stage-run/publication/consent/watcher states |
| `reel.db.models` | the tables above, cut-list validation, username normalisation |
| `reel.db.engine` | `Database`: connection settings (WAL, foreign keys, busy timeout), sessions, `transaction(immediate=...)` |
| `reel.db.versioning` | schema version: `init_database`, `inspect_database`, `open_database`, `upgrade_database`, `backup_database` |
| `reel.db.commands` | `reel db init`, `reel db status`, `reel db upgrade` |

## 6. Files on disk *(planned)*

```
data/reel.db                      SQLite database (built in T-101); reel.db-wal and reel.db-shm
                                  are SQLite's WAL files and belong to it
data/backups/                     database backups made by `reel db upgrade`
data/jobs/<job-id>/audio.wav      16 kHz mono audio for STT and analysis
                  /transcript.raw.json
                  /cutlist.json
                  /cleaned.mp4
                  /transcript.clean.json
                  /metadata.json
                  /thumbnail.jpg
                  /job.log         per-job log, readable (built in T-004)
logs/reel-<role>.jsonl            structured JSON log per process role, rotated (built in T-004)
```

Logging (T-004, D-026): `reel.logging_setup.configure_from_config(cfg, role=...)` installs three outputs on the root logger: console (readable), `logs/reel-<role>.jsonl` (JSON, UTC, rotated) and `data/jobs/<id>/job.log` (readable, per recording). `log_context(job_id=..., stage=...)` adds job fields to every record in the block. A redactor masks secrets in all three outputs.

## 7. Configuration *(built in T-003; conventions in D-025)*

- `config/reel.toml`: folders, thresholds, phrase lists, templates, provider options, worker counts, retention. Copied from `config/reel.example.toml`, which documents every setting with its default.
- `config/groups.toml`: group → YouTube playlist, Slack channel, Classroom course, Core Engine group id, STT vocabulary, folder aliases.
- `config/prompts/*.txt`: LLM prompt templates with `$placeholders` (FR-34).
- `.env` / environment (OS secret store from T-701): tokens and keys only (NFR-5). Never in TOML, never logged or shown.
- Any setting can be overridden with `REEL__SECTION__KEY` environment variables.
- `ConfigProvider` re-reads the files when they change; stages ask it for the current snapshot when they start, so changes apply without a code deploy (NFR-9). An invalid edit keeps the last valid configuration and records the error.

| Module | Role |
|---|---|
| `reel.config.schema` | pydantic models of all sections, defaults and validation rules |
| `reel.config.groups` | group mapping and folder-name matching (case/separator-insensitive, aliases) |
| `reel.config.loader` | discovery, TOML + `.env` reading, overrides, path resolution, error messages, `ConfigProvider` |
| `reel.config.prompts` | prompt files, read on every use |
| `reel.config.secrets` | secret names, `Secrets` (values never printed), secrets required by enabled features |
| `reel.config.check` / `commands` | `reel config check`, `reel config init`, `reel config show` |

## 8. Integrations and ports *(planned)*

| Integration | Implementations | Notes |
|---|---|---|
| ConsentProvider | `http` (Core Engine contract) · `file` (interim registry) · mock server (tests) | Unknown status = opted out (D-019) |
| LibraryPublisher | `http` (Library contract) · `local` (interim, shown in dashboard) | Upsert by `reel-<instance_uuid_hex>-job-<id>` (T-705) |
| YouTube | Data API v3 · dry-run | Privacy constant `unlisted` (D-017); idempotency tag `reel-job-<id>` |
| Slack | Bot token · dry-run | Message metadata carries the job id for idempotency |
| Classroom | Classroom API · dry-run | Announcement with link (default) |
| Transcriber | faster-whisper · fake | Language `hy`, VAD, word timestamps |
| LLMClient | Ollama · fake | JSON-schema structured output + validation |

## 9. Concurrency and performance *(worker runtime built in T-103; rules in D-029)*

- Worker threads in one process (or several processes) share the SQLite queue. `reel worker` runs `workers.count` slots; each slot claims a job and runs its stage in a separate stage thread, so the slot can renew the lease (every third of `workers.lease_seconds`), stop a stage that runs past its timeout, notice a staff cancel, and give the job back when the worker stops.
- Stopping is cooperative: stages call `ctx.check()` / `ctx.wait()` and register `ctx.on_cancel()` for external processes. A stage that does not stop within `workers.stop_grace_seconds` is abandoned and its late result is ignored.
- Stage contract (`reel.queue.stage`): a `StageHandler` with `name`, `heavy`, optional `timeout_seconds` and `run(ctx)`; it returns (optionally a `StageResult` with metrics) or raises `Hold`, `StageFailed` (retryable or not) or any other exception (retryable). Implementations are registered in `reel.stages.default_registry()`; stages without one are not claimed.
- Failures after the last attempt and holds call the notifier hook (FR-33); Slack comes with T-801.
- **Heavy stages** (transcription, render, LLM) are limited by a configurable global count (default 1 on this laptop) to avoid overloading the CPU. Light stages (upload, posting) run in parallel.
- Expected timings on this PC are measured in T-303 and T-1002. Without a GPU, local transcription is the slowest step (risk R-6).

## 10. Security and privacy

- Audio and transcripts are processed locally; no third-party STT/LLM service receives them (R-9).
- Secrets come from the environment or the OS secret store, never from config files or logs.
- The dashboard requires staff login; forms are CSRF-protected; transcripts and recordings are visible only to staff.
- Videos are always `unlisted` (C-1). Consent gate before upload (C-3).

## 11. Directory layout

```
ReelProject/
├── README.md · TASKS.md · PROGRESS.md · DECISIONS.md · ARCHITECTURE.md · CHANGELOG.md · THIRD_PARTY.md
├── pyproject.toml · .gitignore · .env.example
├── config/            reel.example.toml, groups.example.toml, consent.example.json, prompts/
├── src/reel/          application package
│   ├── cli.py, logging_setup.py, runtime.py (reel serve), metrics.py, retention.py
│   ├── config/        settings schema, loader, groups, prompts, secrets, checks, `reel config`
│   ├── db/            tables, connections, schema versions, `reel db`
│   ├── queue/         jobs (state machine), stage contract, worker, `reel worker`, job commands
│   ├── ingest/        folder watcher, identification, dashboard uploads
│   ├── media/         FFmpeg wrappers, test media, render, thumbnails
│   ├── stt/           Transcriber interface, faster-whisper, transcript format, fake
│   ├── llm/           LLMClient interface, Ollama (on demand), meter, fake
│   ├── cleaning/      silences, lesson boundaries, empty board, cut list, transcript remap
│   ├── metadata/      chapters and summary, title/description rules
│   ├── consent/       consent providers (file, HTTP) and mock Core Engine
│   ├── publish/       Google API client, YouTube, Slack, Classroom
│   ├── library/       Academy Library port (local, HTTP) and mock
│   ├── auth/          secret store (Credential Manager), Google OAuth, `reel auth`
│   ├── notify/        staff notifications
│   ├── stages/        import, transcribe, clean, chapters, consent, upload + post
│   └── web/           FastAPI app, security, views, uploads, API, templates, static
├── tests/             unit/, integration/ (FFmpeg, processes, end-to-end)
├── scripts/           setup_ffmpeg/whisper/ollama/person_model, make_test_media, benchmarks, autostart.ps1
├── docs/              PRD (EN/HY), brief, setup/ (Google, autostart), integrations/ (contracts)
├── tools/             downloaded FFmpeg and Ollama (not committed)
├── models/            Whisper, Ollama and person-detector models (not committed)
├── data/              database, job files, uploads, uploaded recordings (not committed)
├── logs/              logs (not committed)
└── inbox/             default watched folder (not committed)
```
