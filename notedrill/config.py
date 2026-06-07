"""Configuration management for NoteDrill."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# Default locations
CONFIG_DIR = Path.home() / ".notedrill"
CONFIG_FILE = CONFIG_DIR / "config.toml"
# Legacy config location for backward compat
LEGACY_CONFIG_DIR = Path.home() / ".quiznote"
LEGACY_CONFIG_FILE = LEGACY_CONFIG_DIR / "config.toml"
DEFAULT_VAULT = Path.cwd() / "notes"


class Config(BaseModel):
    """NoteDrill configuration."""

    vault_path: str = str(DEFAULT_VAULT)
    anthropic_api_key: str = ""
    anthropic_model: str = "sonnet"
    db_path: str = ""  # empty = auto-resolve to {vault_path}/.quiznote.db
    default_count: int = 10
    default_types: list[str] = Field(default_factory=lambda: ["all"])
    default_difficulty: str = "mixed"
    web_port: int = 8080

    def resolve_db_path(self) -> str:
        """Return the actual DB path. If db_path is empty, put it in the vault."""
        if self.db_path:
            return self.db_path
        return str(Path(self.vault_path) / ".notedrill.db")


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file, returning {} if it doesn't exist."""
    if path.exists():
        with open(path, "rb") as f:
            return tomllib.load(f)
    return {}


def load_config() -> Config:
    """Load config from file, falling back to env vars and defaults."""
    data: dict[str, Any] = {}

    # 1. Load from config file (new location first, then legacy)
    config_path = CONFIG_FILE
    if not CONFIG_FILE.exists() and LEGACY_CONFIG_FILE.exists():
        config_path = LEGACY_CONFIG_FILE
    file_data = _load_toml(config_path)
    data.update(file_data)

    # 2. Env vars override file (new prefix first, legacy as fallback)
    env_map = {
        "vault_path": ("NOTEDRILL_VAULT", "QUIZNOTE_VAULT"),
        "anthropic_api_key": ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        "anthropic_model": ("NOTEDRILL_MODEL", "QUIZNOTE_MODEL"),
        "db_path": ("NOTEDRILL_DB", "QUIZNOTE_DB"),
        "web_port": ("NOTEDRILL_PORT", "QUIZNOTE_PORT"),
    }
    for key, (new_env, legacy_env) in env_map.items():
        val = os.environ.get(new_env) or os.environ.get(legacy_env)
        if val:
            data[key] = val
            if key == "web_port":
                data[key] = int(val)

    return Config(**data)


def _toml_escape(s: str) -> str:
    """Escape a string for TOML literal output."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _toml_list(items: list) -> str:
    """Format a list as TOML array."""
    inner = ", ".join(f'"{_toml_escape(str(i))}"' for i in items)
    return f"[{inner}]"


def save_config(config: Config) -> None:
    """Save config to TOML file (never writes API key from env)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f'vault_path = "{_toml_escape(config.vault_path)}"',
        f'anthropic_model = "{_toml_escape(config.anthropic_model)}"',
        f'db_path = "{_toml_escape(config.db_path)}"',
        f"default_count = {config.default_count}",
        f"default_types = {_toml_list(config.default_types)}",
        f'default_difficulty = "{_toml_escape(config.default_difficulty)}"',
        f"web_port = {config.web_port}",
    ]
    # Only write api key if explicitly set (not from env)
    if config.anthropic_api_key and "ANTHROPIC_API_KEY" not in os.environ:
        lines.insert(1, f'anthropic_api_key = "{_toml_escape(config.anthropic_api_key)}"')

    with open(CONFIG_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
