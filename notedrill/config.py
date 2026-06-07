"""Configuration management for QuizNote."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# Default locations
CONFIG_DIR = Path.home() / ".quiznote"
CONFIG_FILE = CONFIG_DIR / "config.toml"
DEFAULT_VAULT = Path.cwd() / "notes"


class Config(BaseModel):
    """QuizNote configuration."""

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
        return str(Path(self.vault_path) / ".quiznote.db")


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file, returning {} if it doesn't exist."""
    if path.exists():
        with open(path, "rb") as f:
            return tomllib.load(f)
    return {}


def load_config() -> Config:
    """Load config from file, falling back to env vars and defaults."""
    data: dict[str, Any] = {}

    # 1. Load from config file
    file_data = _load_toml(CONFIG_FILE)
    data.update(file_data)

    # 2. Env vars override file
    env_map = {
        "vault_path": "QUIZNOTE_VAULT",
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "anthropic_model": "QUIZNOTE_MODEL",
        "db_path": "QUIZNOTE_DB",
        "web_port": "QUIZNOTE_PORT",
    }
    for key, env in env_map.items():
        if val := os.environ.get(env):
            data[key] = val
            if key == "web_port":
                data[key] = int(val)

    return Config(**data)


def save_config(config: Config) -> None:
    """Save config to TOML file (never writes API key from env)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f'vault_path = "{config.vault_path}"',
        f'anthropic_model = "{config.anthropic_model}"',
        f'db_path = "{config.db_path}"',
        f"default_count = {config.default_count}",
        f'default_types = {config.default_types}',
        f'default_difficulty = "{config.default_difficulty}"',
        f"web_port = {config.web_port}",
    ]
    # Only write api key if explicitly set (not from env)
    if config.anthropic_api_key and "ANTHROPIC_API_KEY" not in os.environ:
        lines.insert(1, f'anthropic_api_key = "{config.anthropic_api_key}"')

    with open(CONFIG_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
