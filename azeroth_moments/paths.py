"""XDG-style runtime paths for Azeroth Moments."""

from __future__ import annotations

import os
from pathlib import Path


def expand_user_path(value: str) -> Path:
    return Path(os.path.expanduser(value)).resolve()


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    path = Path(base) / "azeroth-moments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    path = Path(base) / "azeroth-moments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def derivatives_dir() -> Path:
    path = state_dir() / "derivatives"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return state_dir() / "azeroth_moments.db"


def pid_path() -> Path:
    return state_dir() / "azeroth-moments.pid"


def log_path() -> Path:
    return state_dir() / "azeroth-moments.log"


def default_env_candidates() -> list[Path]:
    return [
        config_dir() / ".env",
        Path.cwd() / ".env",
    ]
