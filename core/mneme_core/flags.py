"""In-session flag capture — the near-zero-overhead noticing primitive (spec §7.1)."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from collections.abc import Iterable
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


def _record_key(record: dict) -> str:
    """Stable identity for a flag record, independent of key order."""
    return json.dumps(record, sort_keys=True)


def read_flags(home: Path) -> list[dict]:
    """Every readable flag record, newest last.

    Unparseable lines are skipped, not fatal: a killed distill pipeline or an
    interrupted `mneme flag` write leaves a truncated final line, and a raw
    JSONDecodeError here would take down `distill pending` — the hook's gate —
    and silently disable distillation for good.
    """
    records, bad = _read_flag_lines(home)
    if bad:
        print(
            f"mneme: skipped {bad} unreadable line(s) in {paths.flags_path(home)}",
            file=sys.stderr,
        )
    return records


def _read_flag_lines(home: Path) -> tuple[list[dict], int]:
    p = paths.flags_path(home)
    if not p.exists():
        return [], 0
    records: list[dict] = []
    bad = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            bad += 1
    return records, bad


def clear_flags(home: Path) -> None:
    p = paths.flags_path(home)
    if p.exists():
        p.unlink()


def consume_flags(home: Path, consumed: Iterable[dict]) -> int:
    """Drop exactly the given records, keeping every other line.

    The distiller runs for minutes and the session keeps going while it does, so
    flags captured after the pipeline snapshotted them must survive ingest —
    unlinking the whole file destroys knowledge no distiller ever saw. Lines that
    do not parse are kept too: they are the only remaining trace of a truncated
    write and a human may still want to recover them.
    """
    p = paths.flags_path(home)
    if not p.exists():
        return 0
    pending = Counter(_record_key(r) for r in consumed if isinstance(r, dict))
    kept: list[str] = []
    removed = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            kept.append(line)
            continue
        key = _record_key(record) if isinstance(record, dict) else None
        if key is not None and pending[key] > 0:
            pending[key] -= 1
            removed += 1
            continue
        kept.append(line)
    if kept:
        _atomic_write(p, "".join(line + "\n" for line in kept))
    else:
        p.unlink()
    return removed


def _atomic_write(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
