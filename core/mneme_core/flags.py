"""In-session flag capture — the near-zero-overhead noticing primitive (spec §7.1)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .errors import MnemeError

KINDS = frozenset({"golden-path", "knowledge-issue"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_flag(
    home: Path, text: str, kind: str = "golden-path", session: str | None = None
) -> dict:
    if kind not in KINDS:
        raise MnemeError(f"flag kind must be one of {sorted(KINDS)}: {kind!r}")
    if not text.strip():
        raise MnemeError("flag text must not be empty")
    record = {
        "ts": _now(),
        "session": session or os.environ.get("CLAUDE_SESSION_ID", "unknown"),
        "kind": kind,
        "text": text.strip(),
    }
    paths.ensure_layout(home)
    with paths.flags_path(home).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def read_flags(home: Path) -> list[dict]:
    p = paths.flags_path(home)
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def clear_flags(home: Path) -> None:
    p = paths.flags_path(home)
    if p.exists():
        p.unlink()
