"""Filesystem layout for mneme local state (spec §4.1)."""
from __future__ import annotations

import os
from pathlib import Path


def mneme_home() -> Path:
    env = os.environ.get("MNEME_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".mneme"


def staging_dir(home: Path) -> Path:
    return home / "staging"


def quarantine_dir(home: Path) -> Path:
    return staging_dir(home) / "quarantine"


def repos_dir(home: Path) -> Path:
    return home / "repos"


def logs_dir(home: Path) -> Path:
    return home / "logs"


def registry_path(home: Path) -> Path:
    return home / "registry.json"


def declined_path(home: Path) -> Path:
    return home / "declined.jsonl"


def submitted_path(home: Path) -> Path:
    return home / "submitted.jsonl"


def flags_path(home: Path) -> Path:
    return staging_dir(home) / "flags.jsonl"


def db_path(home: Path) -> Path:
    return home / "mneme.db"


def ensure_layout(home: Path) -> Path:
    for d in (home, staging_dir(home), quarantine_dir(home), repos_dir(home), logs_dir(home)):
        d.mkdir(parents=True, exist_ok=True)
    return home
