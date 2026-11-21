# Reel

Lesson recordings edited and published automatically, ready the same evening.

Reel watches a folder for new lesson recordings and does the whole job:

1. It removes the chatter before and after class, the break and long silences, and
   normalises the audio. It zooms the picture to the part of the board where the lesson's
   writing or slides were, with a small margin.
2. It transcribes the lesson locally, in Armenian.
3. It writes chapters, a title, a description and a thumbnail.
4. It checks photo and video consent.
5. It uploads the video to YouTube as **unlisted**, into the group's playlist.
6. It posts the link to Slack and Google Classroom and adds it to the Academy Library.

Staff follow everything in an Armenian dashboard.

Everything runs on one Windows PC with free software. The speech model and the language
model run locally, so no lesson audio or transcript leaves the PC.

> **Status:** MVP. See [PROGRESS.md](PROGRESS.md) for the state and the known limitations.

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [Configure](#configure)
- [Run](#run)
- [Daily use](#daily-use)
- [Commands](#commands)
- [Troubleshooting](#troubleshooting)
- [Documents](#documents)
- [Folders](#folders)
- [Development](#development)

## Requirements

- **Windows 10 or 11**, 64-bit.
- **Hardware:** 16 GB RAM and about 20 GB of free disk for the tools and models, plus room
  for recordings. An Intel CPU with Quick Sync makes rendering about 3× faster; without
  it, Reel renders on the CPU.
- **Python 3.14**, from the free Python install manager: `py install 3.14`.
- **Google:** a free Google Cloud project for YouTube and Classroom
  ([docs/setup/google.md](docs/setup/google.md)).
- **Slack:** a Slack bot token for the posts and alerts.

## Install

Get the code with [Git for Windows](https://git-scm.com/download/win). The repository is
private, so its owner must first add your GitHub account as a collaborator.

Run these once in PowerShell. Everything, including tools and models, stays inside the project
folder.

```powershell
git clone https://github.com/arshakhakobyan7/Reel-Project.git
cd Reel-Project
py -V:3.14 -m venv .venv
Set-Content .venv\pip.ini "[global]`r`nno-cache-dir = true"   # optional: no pip cache outside the project
.venv\Scripts\python -m pip install -e .                       # add ".[dev]" for tests and lint

.venv\Scripts\python scripts\setup_ffmpeg.py                   # FFmpeg into tools\ffmpeg (checked by SHA-256)
.venv\Scripts\python scripts\setup_whisper.py --model large-v3-turbo   # speech model, 1.6 GB
.venv\Scripts\python scripts\setup_ollama.py --pull gemma3:4b  # local LLM, about 5 GB
.venv\Scripts\python scripts\setup_person_model.py             # person detector, 20 MB

.venv\Scripts\reel config init     # config\reel.toml, config\groups.toml and .env from the examples
mkdir inbox                        # the watched folder (watch_folders in config\reel.toml)
.venv\Scripts\reel db init         # the database data\reel.db
.venv\Scripts\reel user add admin --admin --name "Admin"   # the admin's account (the password is asked)
.venv\Scripts\reel config check    # checks everything; fix what it reports
```

## Configure

All settings have safe defaults. The files are commented, so read them once.

- **`config\reel.toml`** holds the settings:
  - watched folders, the speech model, the cleaning rules, rendering, the consent source;
  - the integrations, and their modes;
  - the dashboard address, retention, the time budget.
  
  Every integration starts in `mode = "dry-run"`, which only records what it would send.
  Switch an integration to `"live"` only after its accounts are set up.
- **`config\groups.toml`** lists one block per group: its other folder names, YouTube
  playlist, Slack channel ID, Classroom course ID, Core Engine id and helpful vocabulary.
- **`.env`** holds the secrets: the Slack bot token, the Google client file path and API
  tokens. It is never committed. The Google token itself is kept in Windows Credential
  Manager after you run `reel auth google` once.
- **`config\consent.json`** is the consent list per group, used until the Core Engine is
  connected ([docs/integrations/core-engine-consent.md](docs/integrations/core-engine-consent.md)).

Any setting can be changed for one run with an environment variable named
`REEL__SECTION__KEY`, for example `REEL__WEB__PORT=8090`.

## Run

```powershell
.venv\Scripts\reel serve
```

`reel serve` starts all of Reel:

- the dashboard at <http://127.0.0.1:8080> (only this computer; to open it from staff
  computers over HTTPS, see [docs/setup/network.md](docs/setup/network.md));
- the folder watcher;
- the workers;
- staff alerts;
- the daily cleanup.

Ctrl+C stops it cleanly. A stage that was running starts again next time.

To start Reel automatically when you sign in to Windows, see
[docs/setup/autostart.md](docs/setup/autostart.md):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 install
```

## Daily use

**Adding a recording.** There are two ways:

- Copy the file into the group's folder under a watched folder, for example
  `inbox\Python 12\Lecture 14 2026-11-19.mp4`. Reel waits until the copy is complete.
- In the dashboard, open **Վերբեռնել** (upload), type the group, choose the files, and upload.
  - A group that `config\groups.toml` does not have yet is added to it at once, with empty
    IDs. Fill in its YouTube playlist, Slack channel and Classroom course there before those
    integrations go live.
  - Several files chosen together are the parts of one lesson; their order can be changed.
  - Large files go in pieces and continue after a dropped connection.

**Following the work.** The dashboard's list shows every recording with its stage pills.
Open a recording to see:

- the cut bar (kept and removed parts, with the reasons);
- chapters, title and description;
- consent checks;
- publication links;
- the full history.

**Watching the result.** A recording's page plays the cleaned video and has a download
link. The file is `data\jobs\<job>\cleaned.mp4`, next to `thumbnail.jpg` and
`metadata.json` (title, description, chapters). While publishing is dry-run, this is the
only copy, and the daily cleanup does not remove it.

**Accounts (T-1013, D-065).** The admin's account is made with `reel user add <name> --admin`.
Other people register on the login page (**Գրանցվել**: first and last name, email, an
optional phone number, a password) and sign in with their email at once. They see only their
own recordings. Their videos are processed but not published: they download the result from
the recording's page. Under **Օգտատերեր** the admin sees every account, its recordings and
its recent actions, and can:

- **Թույլ տալ տեղադրել YouTube-ում**: the account's new uploads are published like the admin's;
- **Փակել հաշիվը**: the account cannot sign in any more; its open sessions end at once.

A user's processed video and uploaded files are deleted 30 days after it is ready
(`[retention] processed_only_days`, 0 = keep). The recording's page shows the last day
to download it.

`[web] registration = false` turns registration off.

**When Reel needs staff.** It sends a Slack alert, and the recording shows one of these:

| What you see | What to do |
|---|---|
| **Սպասում է որոշման** (on hold) for identification | Set the group and lesson number on the recording's page. Reel continues by itself. |
| On hold for consent | Watch the video. **Ընդունել և շարունակել** publishes it anyway; **Չեղարկել** publishes nothing. |
| On hold: **Խոսքը պարզ չէ, բաժիններ չեն գտնվել** (unclear speech) | Watch the video. **Տեղադրել առանց բաժինների** publishes it without chapters. To add chapters, type one per line (`4:30 Ֆունկցիաներ`, times as in the page's player) and press **Պահպանել բաժինները և տեղադրել**. **Չեղարկել** publishes nothing. |
| **Ձախողված** (failed) | Read the error, fix its cause, then **Վերագործարկել** (re-run) from the failed stage. |
| A wrong cut | Change the cleaning setting, then re-run from **Մաքրում** (cleaning). Posts are updated, not posted again. |

A quiet stretch with nobody at the board is removed by two rules: the empty board rule and
the silence rule. To keep such a stretch, relax both rules: `[cleaning.empty_board]` and
`[cleaning.silence]`.

**Metrics.** **Չափումներ** (metrics) shows the time from file to links, the same-evening rate,
the share done without staff, failures per stage and compute time. The time budget
(`[budget]`) warns staff about recordings that take too long.

## Commands

| Command | What it does |
|---|---|
| `reel serve [--no-web/--no-watcher/--no-worker/--no-notifier/--no-cleanup]` | Run Reel (all parts by default). |
| `reel status [job] [--status failed]` | Jobs at a glance, or one job in detail. |
| `reel rerun <job> --from <stage> --by <name>` | Run a job again from a stage. The stages are `import`, `transcribe`, `clean`, `chapters`, `consent`, `upload` and `post`. |
| `reel release <job> [--accept]` / `reel cancel <job>` / `reel retry <job>` | The dashboard's actions, with `--by` and `--note` for the audit log. |
| `reel user add [--admin]/password/disable/enable/list` | Dashboard accounts (`--admin`: sees everything, manages accounts). |
| `reel cert [--name N] [--ip A] [--new-ca] [--show]` | The dashboard's HTTPS certificate for the local network (make, renew, show). |
| `reel auth google` / `status [--check]` / `forget` | Connect to Google once, check the connection, or remove it. |
| `reel cleanup [--dry-run]` | Retention, by hand. |
| `reel config init/check/show`, `reel db init/status/upgrade` | Setup and maintenance. |
| `reel worker`, `reel watch` | Only the workers, or only the watcher. |

## Troubleshooting

| Problem | Check |
|---|---|
| `reel config check` reports errors | It names the setting and what is wrong. The setup commands above install the missing tools and models. |
| Nothing happens to a new file | Is it in a watched folder (`[ingest] watch_folders`) with a supported extension? Is the copy finished? `reel status` shows new jobs. |
| "the recording no longer exists" | The file was moved or renamed after Reel queued it. Put it back, then `reel retry <job>`. |
| Transcription is slow | On this PC, a 100-minute lesson takes about an hour on the CPU. The `[budget]` alert tells you when a recording takes longer than expected. |
| Work stopped overnight, then a stage started again | The PC slept. Keep it plugged in: Windows sleeps on a low battery or a closed lid, and nothing runs then. Reel keeps the PC from sleeping for being idle while it works (`[workers] keep_awake`), and the time asleep is not counted against the stage timeout or the budget. |
| The LLM does not start | `tools\ollama` and `models\ollama` must exist (`scripts\setup_ollama.py`). Reel starts Ollama by itself only when it is needed. |
| "Reel is not connected to Google" / "access was revoked" | Run `reel auth google` again. An External app in *Testing* loses access after 7 days ([docs/setup/google.md](docs/setup/google.md)). |
| YouTube "quota" | Reel waits until the daily quota resets (midnight Pacific Time) and goes on by itself. |
| A video is flagged `youtube_not_unlisted` | YouTube locked it to private. See R-5 in [docs/setup/google.md](docs/setup/google.md). |
| Slack posts fail with `invalid_channel` | Use the channel **ID** (for example `C0123456789`), not its name. |
| The dashboard says "Ձևի ժամկետն անցել է" | The page was open for too long. Reload it and try again. |
| Login refused after several tries | Wait 15 minutes, or reset the password: `reel user password <name>`. |
| The disk fills up | `reel cleanup --dry-run` shows what retention would remove, and what it keeps because the video is not on YouTube. Shorten `[retention]` if needed. |
| Something else | Read `logs\reel-serve.jsonl`, or `data\jobs\<job>\job.log` for one job. Run `reel -v <command>` for details in the terminal. |

## Documents

| File | Content |
|---|---|
| [PROGRESS.md](PROGRESS.md) | State, development log and known limitations (read first) |
| [TASKS.md](TASKS.md) | Plan and task status |
| [DECISIONS.md](DECISIONS.md) | Decisions and their reasons |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Architecture |
| [CHANGELOG.md](CHANGELOG.md) | Changes |
| [THIRD_PARTY.md](THIRD_PARTY.md) | Every dependency and its free licence |
| [docs/setup/](docs/setup/) | Google setup, autostart, the dashboard on the network (HTTPS) |
| [docs/integrations/](docs/integrations/) | Contracts for the Core Engine, the Academy Library and Reel's own API |
| [docs/](docs/) | PRD (English and Armenian) and the original brief |

## Folders

| Folder | Purpose | In git |
|---|---|---|
| `src/reel/` | Application code | yes |
| `tests/` | Automated tests (`unit/`, `integration/`) | yes |
| `config/` | Example configuration and LLM prompts | examples only |
| `scripts/` | Setup and helper scripts | yes |
| `docs/` | PRD, brief, setup guides, integration contracts | yes |
| `tools/` | Downloaded tools (FFmpeg, Ollama) | no |
| `models/` | Downloaded models (Whisper, Ollama, person detector) | no |
| `data/` | Database `reel.db` (with SQLite's `reel.db-wal`/`-shm` files, which must stay together with it), per-job working files, uploads | no |
| `logs/` | Application logs | no |
| `inbox/` | Default watched folder | no |
| `samples/` | Real lesson recordings for local testing (private) | no |

## Development

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest        # the whole suite (about 4 minutes); integration tests need FFmpeg
.venv\Scripts\ruff check . ; .venv\Scripts\ruff format --check .
.venv\Scripts\python scripts\make_test_media.py inbox\test.mp4    # a short synthetic lesson
```
