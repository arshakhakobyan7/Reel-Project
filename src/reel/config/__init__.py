"""Reel configuration: settings, group mapping, prompts and secrets (T-003).

Typical use::

    provider = ConfigProvider(find_config_file())
    cfg = provider.get()                 # re-read when a file changes (NFR-9)
    cfg.settings.workers.count
    cfg.groups.find("python 12")
    cfg.paths.data_dir
    cfg.secrets.get("SLACK_BOT_TOKEN")
    cfg.prompts.render("chapters", transcript=text)
"""

from reel.config.groups import GroupConfig, GroupsFile, normalize_group_key
from reel.config.loader import (
    ConfigError,
    ConfigProvider,
    LoadedConfig,
    ResolvedPaths,
    find_config_file,
    load_config,
)
from reel.config.prompts import PromptError, PromptLibrary
from reel.config.schema import Settings
from reel.config.secrets import SECRET_NAMES, Secrets, required_secrets

__all__ = [
    "SECRET_NAMES",
    "ConfigError",
    "ConfigProvider",
    "GroupConfig",
    "GroupsFile",
    "LoadedConfig",
    "PromptError",
    "PromptLibrary",
    "ResolvedPaths",
    "Secrets",
    "Settings",
    "find_config_file",
    "load_config",
    "normalize_group_key",
    "required_secrets",
]
