"""Checks behind ``reel config check``: the configuration and the environment it points to."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from reel.config.loader import LoadedConfig
from reel.config.secrets import required_secrets
from reel.db.versioning import SchemaState, inspect_database
from reel.media.ffmpeg_check import CapabilityReport, FfmpegError, check_capabilities

Level = Literal["ok", "info", "warn", "error"]
FfmpegChecker = Callable[[Path, Path], CapabilityReport]


@dataclass(frozen=True)
class CheckItem:
    level: Level
    message: str


def run_checks(
    cfg: LoadedConfig, ffmpeg_checker: FfmpegChecker = check_capabilities
) -> list[CheckItem]:
    s, p = cfg.settings, cfg.paths
    items: list[CheckItem] = [CheckItem("ok", "configuration file is valid")]

    def show(path: Path) -> str:
        try:
            return str(path.relative_to(p.base_dir))
        except ValueError:
            return str(path)

    def add(level: Level, message: str) -> None:
        items.append(CheckItem(level, message))

    names = list(cfg.groups.groups)
    if names:
        add("ok", f"groups file {show(p.groups_file)}: {len(names)} group(s): {', '.join(names)}")
    else:
        add("warn", f"groups file {show(p.groups_file)} has no groups yet")

    for folder in p.watch_folders:
        if folder.is_dir():
            add("ok", f"watch folder {show(folder)}")
        else:
            add("error", f"watch folder {show(folder)} does not exist")

    for label, folder in (("data", p.data_dir), ("logs", p.logs_dir), ("models", p.models_dir)):
        if folder.is_dir():
            if os.access(folder, os.W_OK):
                add("ok", f"{label} folder {show(folder)}")
            else:
                add("error", f"{label} folder {show(folder)} is not writable")
        elif folder.parent.is_dir():
            add("info", f"{label} folder {show(folder)} will be created when needed")
        else:
            add("error", f"{label} folder {show(folder)}: its parent folder does not exist")

    database = inspect_database(p.database_file)
    if database.state is SchemaState.CURRENT:
        add("ok", f"database {show(p.database_file)}: schema version {database.version}")
    elif database.state in (SchemaState.MISSING, SchemaState.EMPTY):
        add("warn", f"database {show(p.database_file)} {database.summary()}")
    else:
        add("error", f"database {show(p.database_file)} {database.summary()}")

    try:
        report = ffmpeg_checker(p.ffmpeg, p.ffprobe)
    except FfmpegError as exc:
        add("error", f"FFmpeg: {exc} (run scripts/setup_ffmpeg.py or fix paths.ffmpeg/ffprobe)")
    else:
        if report.ok:
            add("ok", f"FFmpeg: {report.ffmpeg_version}")
        else:
            missing = ", ".join(report.missing_filters + report.missing_encoders)
            add("error", f"FFmpeg lacks required features: {missing}")

    if p.prompts_dir.is_dir():
        add("ok", f"prompts folder {show(p.prompts_dir)}")
    else:
        add("error", f"prompts folder {show(p.prompts_dir)} does not exist")

    if s.consent.provider == "file":
        if p.consent_file.is_file():
            add("ok", f"consent registry {show(p.consent_file)}")
        else:
            add(
                "warn",
                f"consent registry {show(p.consent_file)} not found: every recording will be "
                "held for staff review before upload (D-019)",
            )
    else:
        add("info", f"consent comes from the Core Engine at {s.consent.http_base_url}")

    modes = (
        ("YouTube", s.youtube.mode),
        ("Slack", s.slack.mode),
        ("Classroom", s.classroom.mode),
        ("staff notifications", s.notifications.mode),
    )
    dry_run = [label for label, mode in modes if mode == "dry-run"]
    if dry_run:
        add("info", f"dry-run (nothing is sent): {', '.join(dry_run)}")
    library = (
        "local, inside Reel (interim)" if s.library.provider == "local" else s.library.http_base_url
    )
    add("info", f"Academy Library: {library}")

    # A video without a playlist is still uploaded (flag youtube_no_playlist), and groups
    # typed in the dashboard start with empty IDs (D-061): a warning. Slack and Classroom
    # cannot post without their channel or course: an error.
    group_ids = (
        (
            "youtube_playlist_id",
            s.youtube.mode == "live" and not s.youtube.create_missing_playlists,
            "warn",
            ": their videos are uploaded without a playlist",
        ),
        ("slack_channel", s.slack.mode == "live", "error", ""),
        ("classroom_course_id", s.classroom.mode == "live", "error", ""),
    )
    for attribute, needed, level, result in group_ids:
        missing_groups = [n for n, g in cfg.groups.groups.items() if not getattr(g, attribute)]
        if needed and missing_groups:
            add(level, f"{attribute} is empty for group(s): {', '.join(missing_groups)}{result}")

    needed_secrets = required_secrets(s)
    for name, reason in needed_secrets.items():
        if cfg.secrets.is_set(name):
            add("ok", f"secret {name} is set")
        else:
            add("error", f"secret {name} is missing ({reason}); add it to {show(p.dotenv_file)}")
    client_file = cfg.secrets.get("GOOGLE_CLIENT_SECRETS_FILE")
    client_needed = "GOOGLE_CLIENT_SECRETS_FILE" in needed_secrets
    if client_needed and client_file and not cfg.resolve(client_file).is_file():
        add("error", f"GOOGLE_CLIENT_SECRETS_FILE points to a missing file: {client_file}")
    if not needed_secrets:
        add("ok", "no secrets are needed for the enabled features")

    if cfg.overrides:
        listed = ", ".join(f"{key} ({variable})" for key, variable in cfg.overrides.items())
        add("info", f"overridden by environment variables: {listed}")
    return items
