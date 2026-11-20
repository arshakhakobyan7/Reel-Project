"""``reel config check | init | show``."""

import argparse
import dataclasses
import json
import shutil
import sys
from pathlib import Path

from reel.config.check import run_checks
from reel.config.loader import ConfigError, LoadedConfig, find_config_file, load_config

LABELS = {"ok": "[ OK ]", "info": "[INFO]", "warn": "[WARN]", "error": "[FAIL]"}


def register(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("config", help="check, create or show the configuration")
    actions = parser.add_subparsers(dest="config_action", metavar="<action>", required=True)
    actions.add_parser("check", help="validate the configuration and the environment it uses")
    actions.add_parser(
        "init",
        help="create config/reel.toml, config/groups.toml and .env from the examples "
        "(existing files are never overwritten)",
    )
    actions.add_parser("show", help="print the effective configuration (no secret values)")
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    config_file = find_config_file(args.config)
    if args.config_action == "init":
        return init(config_file)
    try:
        cfg = load_config(config_file)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.config_action == "show":
        return show(cfg)
    return check(cfg)


def check(cfg: LoadedConfig) -> int:
    print(f"Configuration : {cfg.config_file}")
    print(f"Project folder: {cfg.paths.base_dir}")
    items = run_checks(cfg)
    for item in items:
        print(f"  {LABELS[item.level]} {item.message}")
    errors = sum(item.level == "error" for item in items)
    warnings = sum(item.level == "warn" for item in items)
    verdict = "OK" if not errors else "PROBLEMS FOUND"
    print(f"Result: {verdict} - {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def init(config_file: Path) -> int:
    config_file = config_file.resolve()
    config_dir = config_file.parent
    base_dir = config_dir.parent if config_dir.name.lower() == "config" else config_dir
    pairs = (
        (config_dir / "reel.example.toml", config_file),
        (config_dir / "groups.example.toml", config_dir / "groups.toml"),
        (base_dir / ".env.example", base_dir / ".env"),
    )
    status = 0
    for example, target in pairs:
        if target.exists():
            print(f"kept existing {target}")
        elif not example.is_file():
            print(f"ERROR: example file not found: {example}", file=sys.stderr)
            status = 1
        else:
            shutil.copyfile(example, target)
            print(f"created {target}")
    if status == 0:
        print("Next: edit these files, run `reel config check`, then `reel db init`.")
    return status


def show(cfg: LoadedConfig) -> int:
    paths = {
        key: [str(item) for item in value] if isinstance(value, tuple) else str(value)
        for key, value in dataclasses.asdict(cfg.paths).items()
    }
    data = {
        "config_file": str(cfg.config_file),
        "paths": paths,
        "settings": cfg.settings.model_dump(mode="json"),
        "groups": cfg.groups.model_dump(mode="json")["groups"],
        "secrets_set": cfg.secrets.names_set(),
        "environment_overrides": dict(cfg.overrides),
    }
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0
