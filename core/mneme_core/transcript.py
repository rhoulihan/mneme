"""Reading a session transcript into normalised events.

This is the REPLAY adapter of the detector's input contract. A probe against Claude Code
2.1.278 showed hook payloads carry more than their documentation claims -- `PostToolUse`
has the full `tool_response`, `SubagentStop` has the subagent's report -- so production
detection can read payloads directly. But the transcript is the only input available for a
session that has already happened, which is what the acceptance suite replays. One
detector, two adapters: `Event` is what both produce.

A transcript is JSONL written by the harness while the session is still running, so it is
always read from under a live writer. Every parse failure is skipped rather than raised:
losing one line is a rounding error, and losing the session is the entire signal.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

TOOL_USE = "tool_use"
TOOL_RESULT = "tool_result"
TEXT = "text"


@dataclass
class Event:
    kind: str
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    tool_use_id: str = ""
    text: str = ""
    is_error: bool = False
    sidechain: bool = False
    ts: str = ""


@dataclass
class Outcome:
    """One tool call and how it ended -- the fail->success transition T2 detects."""

    tool_name: str
    command: str
    failed: bool
    tool_use_id: str
    sidechain: bool = False


def _blocks(record: dict) -> list:
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    return content if isinstance(content, list) else []


def subagent_paths(path: Path | str) -> list[Path]:
    """The transcripts of subagents this session delegated to.

    Claude Code does NOT mark delegated turns in the main transcript -- `isSidechain` is
    absent from every real transcript on this machine. It writes them to
    `<transcript-stem>/subagents/agent-*.jsonl` instead. Counting only the main file
    understates a delegated session badly: one real session here shows 1,290 main tool
    calls and 724 more across 19 subagent files.
    """
    main = Path(path)
    directory = main.parent / main.stem / "subagents"
    try:
        return sorted(p for p in directory.glob("*.jsonl") if p.is_file())
    except OSError:
        return []


def _read_one(path: Path, *, sidechain: bool) -> list[Event]:
    out: list[Event] = []
    try:
        # Streamed, never slurped: a real transcript on this machine reaches 330MB, and
        # `read_text()` + `splitlines()` materialises it twice -- 2.8GB of RSS on a hook
        # that fires at the end of EVERY turn.
        #
        # `newline="\n"` disables universal-newline translation so the split is on \n
        # alone. `str.splitlines()` breaks on ten characters, three of them non-ASCII,
        # and Node's `JSON.stringify` leaves U+2028, U+2029 and U+0085 RAW inside
        # strings -- verified. Splitting on those cuts a valid record in half, both
        # halves fail to parse, and the whole assistant message vanishes from the count.
        # This repo has now fixed that class three times.
        with path.open(encoding="utf-8", errors="replace", newline="\n") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                except RecursionError:
                    # Deeply nested JSON blows the C scanner's stack and raises
                    # RecursionError, which is NOT a ValueError. `proposals.py` guards the
                    # identical boundary for the identical reason: one hostile line is a
                    # skipped line, never a lost session.
                    continue
                if not isinstance(record, dict):
                    continue
                out.extend(_events_from(record, sidechain=sidechain))
    except OSError:
        # Absent or unreadable is a session we cannot describe, not an error the caller
        # can act on -- and this runs inside a hook, where raising takes the session down.
        return []
    return out


def read_events(path: Path | str, *, include_subagents: bool = True) -> list[Event]:
    events = _read_one(Path(path), sidechain=False)
    if include_subagents:
        for sub in subagent_paths(path):
            events.extend(_read_one(sub, sidechain=True))
    return events


def _events_from(record: dict, *, sidechain: bool) -> list[Event]:
    out: list[Event] = []
    if True:
        sidechain = sidechain or bool(record.get("isSidechain"))
        ts = str(record.get("timestamp", ""))
        for block in _blocks(record):
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == TOOL_USE:
                raw_input = block.get("input")
                out.append(
                    Event(
                        kind=TOOL_USE,
                        tool_name=str(block.get("name", "")),
                        tool_input=raw_input if isinstance(raw_input, dict) else {},
                        tool_use_id=str(block.get("id", "")),
                        sidechain=sidechain,
                        ts=ts,
                    )
                )
            elif kind == TOOL_RESULT:
                out.append(
                    Event(
                        kind=TOOL_RESULT,
                        tool_use_id=str(block.get("tool_use_id", "")),
                        text=_as_text(block.get("content")),
                        is_error=bool(block.get("is_error")),
                        sidechain=sidechain,
                        ts=ts,
                    )
                )
            elif kind == TEXT:
                out.append(
                    Event(kind=TEXT, text=str(block.get("text", "")),
                          sidechain=sidechain, ts=ts)
                )
    return out


# A `mneme ... flag` invocation at the START of the command, after optional environment
# assignments and an optional path. Counting what a session CAPTURED is the only question
# `flags.jsonl` cannot answer -- the distiller consumes flags with --clear-flags, so a
# session that flagged well and got distilled reads as one that flagged nothing.
#
# Anchored to the string start, NOT multiline, and that is the whole design. Searching
# anywhere matched the words inside a heredoc: on mneme's own development sessions it
# counted `cat > spec.md <<'EOF' ... mneme flag ... EOF` as eleven captures, and since any
# non-zero count suppresses the report, mneme stopped noticing its own silent sessions.
#
# The cost is a false negative on `cd /x && mneme flag "y"`. That is the right direction
# to err: a missed flag over-reports an omission, which is noise; a phantom flag silences
# the report, which is the exact failure this feature exists to remove.
_FLAG_CMD_RE = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"   # FOO=bar prefixes
    r"(?:[^\s;|&]*/)?mneme\b"                    # optional ./bin/ or /usr/local/bin/
    r"[^\n;|&]*\bflag\b"
)
_FLAG_TOOLS = ("mneme_flag",)


def flag_invocations(events: list[Event]) -> int:
    """How many times this session flagged something, from its own record of doing it."""
    n = 0
    for e in events:
        if e.kind != TOOL_USE:
            continue
        if any(e.tool_name.endswith(t) for t in _FLAG_TOOLS):
            n += 1
            continue
        command = str(e.tool_input.get("command", ""))
        if command and _FLAG_CMD_RE.search(command):
            n += 1
    return n


def _as_text(content: object) -> str:
    """Tool result content is a string for some tools and a block list for others."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == TEXT
        ]
        return "\n".join(parts)
    return ""


def tool_calls(events: list[Event], *, include_sidechain: bool = True) -> int:
    return sum(
        1
        for e in events
        if e.kind == TOOL_USE and (include_sidechain or not e.sidechain)
    )


def outcomes(events: list[Event], *, include_sidechain: bool = True) -> list[Outcome]:
    """Pair each call with its result, in call order.

    Paired by `tool_use_id` and never by position: a result arrives in a later record than
    its call, results can interleave, and a background task's result can arrive long after
    the calls that follow it.
    """
    results = {
        e.tool_use_id: e for e in events if e.kind == TOOL_RESULT and e.tool_use_id
    }
    out: list[Outcome] = []
    for e in events:
        if e.kind != TOOL_USE:
            continue
        if not include_sidechain and e.sidechain:
            continue
        result = results.get(e.tool_use_id)
        out.append(
            Outcome(
                tool_name=e.tool_name,
                command=str(e.tool_input.get("command", "")),
                failed=bool(result.is_error) if result is not None else False,
                tool_use_id=e.tool_use_id,
                sidechain=e.sidechain,
            )
        )
    return out
