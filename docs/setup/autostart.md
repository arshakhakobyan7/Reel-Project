# Starting Reel automatically (Windows)

You can make Reel start by itself on this PC, so it runs without anyone starting it by
hand (T-903). `reel serve` runs every part of Reel in one process: the dashboard, the
folder watcher, the workers, staff alerts and the daily cleanup.

## Install

Open PowerShell in the project folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 install
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 start
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 status
```

- `install` adds the Task Scheduler task **Reel** for your Windows user.
- From then on, Reel starts whenever this user signs in. If the PC was off at sign-in time,
  it starts as soon as it can.
- `start` starts it now without signing out. `status` should then say `Running`.
- You don't need administrator rights.

Check that Reel runs:

- Open <http://127.0.0.1:8080>. The dashboard's login page should appear.
- Look at `logs\reel-serve.jsonl`, Reel's log. It should contain `Reel started: notifier,
  watcher, worker, cleanup, web`.

## How the task behaves

- **No window:** Reel runs with `.venv\Scripts\pythonw.exe -m reel serve`, so no console
  window opens.
- **Restarts:** if Reel stops because of an error, the task starts it again up to 3 times,
  one minute apart.
- **One copy:** a second copy is never started.
- **Battery:** Reel also runs on battery.
- **No time limit:** the task can run for any length of time.
- **Only while signed in:** the task runs only while the user is signed in, so Windows
  stores no password. Lock the screen instead of signing out. Reel keeps working while the
  screen is locked.
- **After a crash:** if Reel or the PC stopped in the middle of a job, the next start
  continues the job from its stage (D-052).

## Stop or remove

```powershell
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 stop
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 uninstall
```

- Stopping the task stops Reel at once. A stage that was running when Reel stopped runs again
  at the next start.
- For a clean stop, run Reel by hand in a terminal (`reel serve`) and press Ctrl+C.

## Options

To run only some parts of Reel, pass the `reel` arguments when you install. For example, to
run Reel without the dashboard:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 install -Arguments "serve --no-web"
```

## Why there is no Docker image

The plan offered an optional Dockerfile for Linux. It is not provided:

- Reel keeps its Google token in Windows Credential Manager (D-047), and it protects the
  YouTube upload session with Windows DPAPI (T-702). On Linux, Reel has only its
  development file store for secrets, which is not safe for real use.
- Rendering uses Intel Quick Sync on this PC.
- Docker is not installed here, and it is not needed (D-009).

If Reel ever has to run on a Linux server, it first needs a Linux secret store. That would be
a new task.
