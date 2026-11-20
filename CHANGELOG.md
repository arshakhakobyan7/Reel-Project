# Changelog

All notable changes to Reel. The day-by-day log is in [PROGRESS.md](PROGRESS.md), and the
reasons for each change are in [DECISIONS.md](DECISIONS.md).

## 0.1.0 — 2026-11-20 (MVP, not yet released)

### Pipeline

- **Watching and importing:** Reel watches folders and waits until a file stops changing. It
  then works out the group, the lesson and the date from the path and the file. It finds
  duplicates by content hash and checks that the file is a usable recording.
- **Transcription:** local Whisper (large-v3-turbo, int8), Armenian, with no cloud service.
- **Cleaning:**
  - finds where the lesson starts and ends from phrases, with the local LLM's answers checked
    against the transcript;
  - shortens time with nobody at the board to 5 seconds (YOLOX person detector);
  - removes long silences;
  - keeps a cut list with a reason for every cut;
  - renders with Intel Quick Sync, or libx264 when it is missing, with two-pass loudness
    normalisation to −16 LUFS;
  - maps the transcript onto the cleaned video.
- **Lessons in parts:** a lesson recorded in several parts becomes one video.
- **Chapters, title and description:** chapters start at real transition phrases. The title,
  description and topic summary follow YouTube's rules, and a thumbnail is made.
- **Consent:** the consent check uses a file now; the Core Engine contract is ready. Reel
  holds the job when a student opted out or a status is unknown; staff decide, and the
  decision is audited.
- **Publishing:**
  - YouTube upload, always unlisted, resumable, into the group playlist; a video replaced by
    a re-run leaves the playlist;
  - posts to Slack and Classroom, and an entry in the Library;
  - each target is retried on its own, and nothing is posted twice;
  - every integration has a dry-run mode.

### Operations

- **Dashboard** (Armenian):
  - recordings list with stage pills and filters, and a recording detail page with the cut
    bar, chapters, consent, publications and history;
  - staff actions: release, cancel, set the group and lesson, re-run from a stage, retry now;
  - resumable upload of large files and lessons in parts;
  - metrics: time from file to links, same-evening rate, share without staff, failures per
    stage, compute time and LLM tokens, a time budget.
- **Security:**
  - staff login (scrypt passwords, signed session cookie);
  - CSRF on every form;
  - throttled logins;
  - a strict Content Security Policy.
- **Running:**
  - `reel serve` runs everything in one process. After a crash, jobs continue from their
    stage at once;
  - staff alerts go to Slack;
  - data retention;
  - Windows autostart (Task Scheduler);
  - terminal commands: `status`, `rerun`, `release`, `cancel`, `retry`, `cleanup`, `user`,
    `auth`, `config`, `db`.
- **For other teams:** a read-only integration API (`/api/v1`).
- **Google sign-in:** OAuth sign-in once; the token is kept in Windows Credential Manager.

### Known limitations

These are listed at the end of PROGRESS.md (T-1005).
