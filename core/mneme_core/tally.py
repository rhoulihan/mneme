"""Per-session capture tallies — making the omission loud.

The requirements doc's RC4 is that omission is silent, and it is the load-bearing factor:
a process that is supposed to happen, with nothing forcing it and no signal when it
doesn't, will drift. mneme already implements that failure exactly. Its `Stop` hook runs

    "$ROOT/bin/mneme" distill pending >/dev/null 2>&1 || exit 0

and `distill pending` prints the pending flag count, exiting 1 when it is zero -- so the
one session worth reporting is the one where the count is computed, redirected to
/dev/null, and dropped.

A probe against Claude Code 2.1.278 fixes the shape of the fix: a `Stop` hook cannot inject
context, and its stdout is discarded outright. So `Stop` WRITES a tally here and a later
`SessionStart` -- which already injects mneme's noticing brief -- speaks it. Seeing and
speaking are different events.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .errors import MnemeError

# One record per session, forever, is unbounded growth. Trimmed on write for the same
# reason `proposals.py` caps every untrusted string.
MAX_RECORDS = 200
# Below this, a session with no flags is someone asking a question, not a capture failure.
# N4: a session producing nothing should produce no noise.
MIN_TOOL_CALLS = 20
# A session id is a UUID. Capped because it arrives from a hook payload or the
# environment and is written to a ledger re-read on every Stop and every SessionStart.
MAX_SESSION_ID = 200


@dataclass
class SessionTally:
    session: str
    tool_calls: int
    flags: int
    candidates: int = 0
    unflagged: int = 0
    ts: str = ""
    # Spoken once. Without this the same tally is re-injected at every later
    # SessionStart, where "Last session" becomes false from the second time on and a
    # nudge that repeats verbatim is exactly the noise N4 forbids.
    reported: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(home: Path, entry: SessionTally) -> None:
    """Write one session's tally, replacing any earlier one for the same session.

    Locked and atomic. `Stop` is `async` and fires once per TURN, `PreCompact` runs the
    same script, and a user commonly has several sessions open on one MNEME_HOME -- so
    this read-modify-write races itself. Measured unlocked: 3 processes x 60 records left
    10 of 180, because each writer rewrote the whole file from a stale snapshot. The loss
    is other sessions' records, not the racing writer's own.
    """
    entry.session = entry.session[:MAX_SESSION_ID]
    if not entry.ts:
        entry.ts = _now()
    paths.ensure_layout(home)
    with paths.locked(home, "staging"):
        existing = [t for t in read_tallies(home) if t.session != entry.session]
        existing.append(entry)
        _write_all(home, existing[-MAX_RECORDS:])


def mark_reported(home: Path, session: str) -> None:
    with paths.locked(home, "staging"):
        entries = read_tallies(home)
        touched = False
        for t in entries:
            if t.session == session and not t.reported:
                t.reported = True
                touched = True
        if touched:
            _write_all(home, entries)


def _write_all(home: Path, entries: list[SessionTally]) -> None:
    """Atomic: a Stop hook is killed when its session exits, and `write_text` truncates
    before it writes, so an interrupted save would leave an empty or half-written ledger."""
    target = paths.sessions_path(home)
    tmp = target.with_suffix(".jsonl.tmp")
    tmp.write_text(
        "".join(json.dumps(asdict(t)) + "\n" for t in entries), encoding="utf-8"
    )
    os.replace(tmp, target)


def read_tallies(home: Path) -> list[SessionTally]:
    p = paths.sessions_path(home)
    try:
        # errors="replace": an undecodable ledger must not raise out of `mneme context`,
        # where the hook's `|| exit 0` would drop the noticing brief and the registry
        # summary along with it.
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    known = {f for f in SessionTally.__dataclass_fields__}
    out: list[SessionTally] = []
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
            out.append(SessionTally(**{k: v for k, v in data.items() if k in known}))
        except TypeError:
            continue
    if raw.strip() and not out:
        # Corruption is not emptiness. `registry._read_document` learned this the
        # expensive way: reading a damaged file as empty let the next write delete what
        # survived. Lower stakes here -- these records are advisory and regenerate on the
        # next Stop -- but the failure mode is identical, so it is refused rather than
        # papered over.
        raise MnemeError(
            f"the session ledger at {p} has content but no readable records;"
            " remove the file to start a fresh one."
        )
    return out


def is_notable(entry: SessionTally, *, min_tool_calls: int = MIN_TOOL_CALLS) -> bool:
    """Worth saying out loud: real work happened and nothing was captured, or something
    was detected and left unflagged."""
    if entry.unflagged > 0:
        return True
    return entry.flags == 0 and entry.tool_calls >= min_tool_calls


def for_session(home: Path, session: str) -> SessionTally | None:
    for entry in read_tallies(home):
        if entry.session == session:
            return entry
    return None


def last_notable(
    home: Path, *, exclude: str | None = None, min_tool_calls: int = MIN_TOOL_CALLS
) -> SessionTally | None:
    for entry in reversed(read_tallies(home)):
        if exclude is not None and entry.session == exclude:
            continue
        if entry.reported:
            continue
        if is_notable(entry, min_tool_calls=min_tool_calls):
            return entry
    return None


def render(entry: SessionTally, *, current: bool = False) -> str:
    """One line, stated plainly. A tally nobody reads is the silence it replaces."""
    lead = "This session so far" if current else "Last session"
    parts = [f"{lead}: {entry.tool_calls} tool calls, {entry.flags} flags captured."]
    if entry.unflagged:
        parts.append(f"{entry.unflagged} candidate(s) were detected and not flagged.")
    if entry.flags == 0:
        parts.append(
            "If nothing durable was learned that is a fine answer — say so; if something"
            " was, flag it now before it is gone."
        )
    return " ".join(parts)
