"""Candidates the detector surfaced, and whether the user has been told.

Detection happens where the knowledge appears (`Stop`, over the transcript). Delivery
happens at `UserPromptSubmit`, the only event that fires repeatedly during a session AND
can put text in front of the model -- verified against Claude Code 2.1.278. Seeing and
speaking are different events, so something has to hold a candidate between them.

Landing at a user-turn boundary is not a consolation prize for `Stop` being mute. The
requirements doc's RC3 says "at the moment it happens" is the WORST moment, because the
flag competes with processing the finding. A turn boundary is the fix RC3 asks for, and it
rate-limits itself to once per user message.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths

MAX_RECORDS = 500
# N2, the interruption budget. The channel already limits this to once per user message;
# this limits how much arrives when it does. A wall of candidates is ignored wholesale.
MAX_PER_PROMPT = 3
MAX_FIELD = 400


@dataclass
class Noticed:
    session: str
    kind: str
    evidence: str
    detail: str = ""
    ts: str = ""
    reported: bool = False

    def key(self) -> str:
        return f"{self.session}\x00{self.kind}\x00{self.detail}\x00{self.evidence}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_noticed(home: Path) -> list[Noticed]:
    p = paths.noticed_path(home)
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    known = set(Noticed.__dataclass_fields__)
    out: list[Noticed] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        try:
            out.append(Noticed(**{k: v for k, v in data.items() if k in known}))
        except TypeError:
            continue
    return out


def _write_all(home: Path, entries: list[Noticed]) -> None:
    target = paths.noticed_path(home)
    tmp = target.with_suffix(".jsonl.tmp")
    tmp.write_text(
        "".join(json.dumps(asdict(e)) + "\n" for e in entries), encoding="utf-8"
    )
    os.replace(tmp, target)


def record_signals(home: Path, session: str, signals: list) -> int:
    """Persist candidates not already held for this session. Returns how many were new."""
    paths.ensure_layout(home)
    with paths.locked(home, "staging"):
        existing = read_noticed(home)
        seen = {e.key() for e in existing}
        fresh: list[Noticed] = []
        for s in signals:
            entry = Noticed(
                session=session[:MAX_FIELD],
                kind=str(getattr(s, "kind", ""))[:MAX_FIELD],
                evidence=str(getattr(s, "evidence", ""))[:MAX_FIELD],
                detail=str(getattr(s, "detail", ""))[:MAX_FIELD],
                ts=_now(),
            )
            if entry.key() in seen:
                continue
            seen.add(entry.key())
            fresh.append(entry)
        if fresh:
            _write_all(home, (existing + fresh)[-MAX_RECORDS:])
        return len(fresh)


def unreported(home: Path, session: str, limit: int = MAX_PER_PROMPT) -> list[Noticed]:
    return [e for e in read_noticed(home) if e.session == session and not e.reported][
        :limit
    ]


def mark_reported(home: Path, entries: list[Noticed]) -> None:
    if not entries:
        return
    keys = {e.key() for e in entries}
    with paths.locked(home, "staging"):
        held = read_noticed(home)
        touched = False
        for e in held:
            if e.key() in keys and not e.reported:
                e.reported = True
                touched = True
        if touched:
            _write_all(home, held)


def count_for(home: Path, session: str) -> int:
    return sum(1 for e in read_noticed(home) if e.session == session)


def render(entries: list[Noticed]) -> str:
    """Specific, not generic. R2's own standard: a generic "flag anything useful?" is
    dismissed; naming the error code and what resolved it is not."""
    if not entries:
        return ""
    lines = ["mneme noticed while you worked — flag anything durable, one line each:"]
    for e in entries:
        what = e.detail or e.evidence
        lines.append(f"- [{e.kind}] {what}")
    lines.append(
        "Flag with the `mneme_flag` tool (no quoting needed), or"
        ' `mneme flag "<what worked + why it was non-obvious>"` —'
        " or say nothing if none of it is durable."
    )
    return "\n".join(lines)
