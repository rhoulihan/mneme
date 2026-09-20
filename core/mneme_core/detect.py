"""Detecting moments where durable knowledge was probably just produced.

The requirements doc's R2: surface SPECIFIC candidates, never a generic reminder. A
generic "flag anything useful?" is dismissed; "this turn contained ORA-40454 and a
subsequent success -- flag it?" is not.

Precision over recall, deliberately and repeatedly. A noisy detector is switched off
within a day, and a detector nobody runs has worse recall than a quiet one. Every rule
here is mechanical and conservative; none of them asks a model anything.

The input is `transcript.Event`, which both adapters produce -- hook payloads in
production, a transcript on replay -- so the regression corpus exercises the same code
that ships.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .transcript import TEXT, TOOL_RESULT, TOOL_USE, Event

# Vendor error codes. Anchored to the vendor's own shape rather than the word "error",
# which appears in ordinary output constantly.
_ERROR_CODE_RE = re.compile(
    r"\b("
    r"ORA-\d{4,5}"           # Oracle
    r"|PLS-\d{4,5}"
    r"|SP2-\d{4,5}"
    r"|SQLSTATE\[?\w+"       # ANSI SQL
    r"|ERRCODE_\w+"
    # An explicit errno list, never a pattern. `E[A-Z]{3,}` matched EMAIL, EVERY, EXIT
    # and ERROR in ordinary output, and every one of those became a "vendor error code".
    r"|E(?:NOENT|ACCES|PERM|EXIST|NOTDIR|ISDIR|INVAL|NOSPC|PIPE|AGAIN|INTR|IO|BADF"
    r"|CONNREFUSED|CONNRESET|TIMEDOUT|ADDRINUSE|MFILE|NFILE|NOMEM|ROFS|XDEV|LOOP|2BIG)"
    r"|[A-Z][a-z]+Error"     # TypeError, RecursionError
    r"|SIG[A-Z]{3,}"         # SIGKILL
    r")\b"
)

# "This is not what you would have assumed." Run against assistant prose only: these words
# are noise inside tool output and signal inside an explanation.
_SURPRISE_RE = re.compile(
    r"\b("
    r"turns out|actually|it turns out|contrary to|surprisingly"
    r"|silently|in fact|despite|counterintuitiv\w+"
    r"|does not actually|is not actually|never actually"
    r"|the real (?:cause|reason|problem)"
    r")\b",
    re.IGNORECASE,
)

# A measured boundary: a number carrying a unit, next to threshold language.
_MEASURED_RE = re.compile(
    r"\b\d[\d,._]*\s?"
    r"(?:ms|µs|us|ns|s|sec|secs|seconds|min|mins|hours?|days?"
    r"|[KMGT]?i?B|bytes?|rows?|%|x)\b",
    re.IGNORECASE,
)
_THRESHOLD_RE = re.compile(
    r"\b(?:limit|threshold|cap|maximum|minimum|max|min|ceiling|floor|beyond|past"
    r"|exceeds?|over|under|crossover|boundary|budget|quota)\b",
    re.IGNORECASE,
)

# Something concrete enough to be a fact ABOUT something: an identifier, a path, a flag,
# an address, or quoted code. The surprise rule requires one, because "actually" next to
# nothing in particular is how people talk, and a detector that fires on conversation is
# a detector that gets muted. A measured value or an error code counts too.
_TECHNICAL_RE = re.compile(
    r"(`[^`]+`"                       # `quoted code`
    r"|\b\w+_\w+\b"                   # snake_case identifiers: pg_hba, JSON_TRANSFORM
    r"|\s--?[a-zA-Z][\w-]+"            # --flags
    r"|/[\w.-]+/[\w./-]*"              # paths
    r"|\b\d{1,3}(?:\.\d{1,3}){3}\b"    # addresses
    r"|\b[a-z]+\([^)]*\)"              # call(...) syntax
    r")"
)

# Commands whose job is to MOVE TEXT. A vendor error code in their output is something
# they displayed, not a fault they hit -- `cat server.log` printing ORA-00942 says nothing
# about cat. Without this, the top of the delivery queue was
# "ERRCODE_INTERNAL_ERROR then a success of `echo`".
#
# The inverse is what makes the rule work at all: only 83 of 5,095 results in the replay
# corpus set `is_error`, because a database error arrives with EXIT CODE 0 -- `docker exec
# ... sqlplus` succeeds while printing ORA-00942. Requiring a non-zero exit threw away
# almost every real finding, so for everything that is not a text-mover, a vendor code in
# the output IS the failure.
_DISPLAY_COMMANDS = frozenset(
    "cat echo sed grep ls head tail awk printf tee less more find wc sort uniq cut tr"
    " jq column diff git".split()
)
RETRY_THRESHOLD = 3
# T2's actual wording: "failure -> success after >=2 PRIOR failures of the same shape".
# One failure then a success is ordinary iteration -- on the replay corpus it fired 485
# times in one session, almost all of it `python3` or `curl` being run again.
MIN_FAILURES = 2
MAX_EVIDENCE = 240


@dataclass
class Signal:
    kind: str
    evidence: str
    anchor: str = ""
    detail: str = field(default="")
    # How hard-won: the number of failures that preceded the fix. Used to order delivery.
    weight: int = 0


def _brief(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:MAX_EVIDENCE]


# A second word is part of the shape only when it looks like a SUBCOMMAND — `git status`
# is a different operation from `git push`, while `sqlplus @load.sql` and
# `sqlplus @load-fixed.sql` are one operation attempted twice. Including the argument made
# every retry look like a new command and the resolved-error rule never fired once.
_SUBCOMMAND_RE = re.compile(r"^[a-z][a-z0-9-]*$")


# `cd <somewhere>` prefixes carry no information about what was attempted, and nearly
# every command in a real session starts with one. Without stripping them, every command
# in the corpus had the shape `cd`, the whole session collapsed into a single bucket, and
# the resolved-error rule paired unrelated failures with unrelated successes -- 521 of
# them, every one junk.
_CD_PREFIX_RE = re.compile(r"^\s*cd\s+[^\n;&|]+(?:\s*(?:&&|;)\s*|\n)")


def _strip_cd(command: str) -> str:
    previous = None
    while previous != command:
        previous = command
        command = _CD_PREFIX_RE.sub("", command, count=1)
    return command


def _command_shape(command: str) -> str:
    """What makes two attempts 'the same thing tried differently'."""
    command = _strip_cd(command)
    words = [w for w in command.split() if "=" not in w]
    if not words:
        return ""
    shape = words[0].rsplit("/", 1)[-1]
    if shape == "cd":
        # A command that is only `cd somewhere`, or one whose prefix the stripper could
        # not see past. Either way it names no operation, so it identifies nothing.
        return ""
    if len(words) > 1 and _SUBCOMMAND_RE.match(words[1]):
        shape += " " + words[1]
    return shape


def resolved_errors(events: list[Event]) -> list[Signal]:
    """A failure carrying a vendor error code, followed by a success of the same shape.

    This is mneme's own stated capture criterion -- "a hard-won fix after real dead ends"
    -- and nothing in the shipped product detects it.
    """
    out: list[Signal] = []
    pending: dict[str, tuple[int, str, str]] = {}
    seen: set[tuple[str, str]] = set()
    results = {e.tool_use_id: e for e in events if e.kind == TOOL_RESULT and e.tool_use_id}
    for e in events:
        if e.kind != TOOL_USE:
            continue
        command = str(e.tool_input.get("command", ""))
        shape = _command_shape(command)
        if not shape:
            continue
        result = results.get(e.tool_use_id)
        if result is None:
            continue
        codes = _ERROR_CODE_RE.findall(result.text)
        display = shape.split()[0] in _DISPLAY_COMMANDS
        failed = result.is_error or (bool(codes) and not display)
        if failed:
            count, code, evidence = pending.get(shape, (0, "", ""))
            pending[shape] = (
                count + 1,
                code or (codes[0] if codes else ""),
                evidence or _brief(result.text),
            )
        elif shape in pending:
            count, code, evidence = pending.pop(shape)
            # A named vendor code is what separates "I learned something about this
            # system" from "my command had a typo in it".
            if count < MIN_FAILURES or not code:
                continue
            key = (shape, code)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Signal(
                    kind="resolved-error",
                    evidence=evidence,
                    anchor=e.tool_use_id,
                    detail=f"{code} then a success of `{shape}`, after {count} failures",
                    weight=count,
                )
            )
    # Hardest-won first. Only a few candidates are delivered per turn (N2), so the order
    # decides which ones a user ever sees -- and four failures before a success is a
    # better bet than two.
    out.sort(key=lambda s: -s.weight)
    return out


def retried_commands(events: list[Event]) -> list[Signal]:
    """The same command attempted many times with differing arguments — someone was
    working something out."""
    attempts: dict[str, set[str]] = {}
    anchors: dict[str, str] = {}
    for e in events:
        if e.kind != TOOL_USE:
            continue
        command = str(e.tool_input.get("command", ""))
        shape = _command_shape(command)
        if not shape:
            continue
        attempts.setdefault(shape, set()).add(command)
        anchors[shape] = e.tool_use_id
    return [
        Signal(
            kind="retried",
            evidence=shape,
            anchor=anchors[shape],
            detail=f"{len(variants)} distinct attempts",
        )
        for shape, variants in attempts.items()
        if len(variants) >= RETRY_THRESHOLD
    ]


def surprises(events: list[Event]) -> list[Signal]:
    """Negation-of-expectation, in the assistant's own prose."""
    out: list[Signal] = []
    for e in events:
        if e.kind != TEXT or not e.text:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", e.text):
            m = _SURPRISE_RE.search(sentence)
            if not m:
                continue
            # A bare "actually" is conversational filler; paired with something concrete
            # it is a finding.
            if (
                _ERROR_CODE_RE.search(sentence)
                or _MEASURED_RE.search(sentence)
                or _TECHNICAL_RE.search(sentence)
            ):
                out.append(
                    Signal(kind="surprise", evidence=_brief(sentence),
                           detail=m.group(1).lower())
                )
    return out


def measured_bounds(events: list[Event]) -> list[Signal]:
    """A number with a unit, stated as a boundary."""
    out: list[Signal] = []
    for e in events:
        if e.kind != TEXT or not e.text:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", e.text):
            if _MEASURED_RE.search(sentence) and _THRESHOLD_RE.search(sentence):
                out.append(Signal(kind="measured", evidence=_brief(sentence)))
    return out


# Measured on the replay corpus -- one real 5,034-call session with 106 hand-captured
# flags (`docs/superpowers/specs/2026-09-18-automatic-capture.md` §6, criterion 5):
#
#     resolved-error   21 signals   plausible; includes ORA-01000, one of the facts the
#                                   requirements doc names by hand
#     retried          91 signals
#     surprise         72 signals   fires on the assistant's own status prose
#     measured         82 signals   ditto
#
# So only `resolved-error` is on by default. The other three are kept, tested, and opt-in:
# R2's binding constraint is "precision over recall -- a noisy detector will be disabled
# by users within a day", and 245 prompts in one session is not a near miss, it is the
# failure mode. Turning them on is a decision with evidence attached, not a default.
DEFAULT_RULES = ("resolved-error",)
ALL_RULES = ("resolved-error", "retried", "surprise", "measured")

_RULES = {
    "resolved-error": resolved_errors,
    "retried": retried_commands,
    "surprise": surprises,
    "measured": measured_bounds,
}


# A subagent's REPORT is not a session transcript, and the base rate is what makes the
# difference. Measured over 787 real subagent reports on this machine: median 3,277 chars,
# 722 substantive, and 216 of those (29%) carry a surprise or measured signal -- roughly 9
# candidates from a 29-subagent session. The same rules over a whole session's assistant
# prose are dominated by status reporting and were switched off for it.
#
# This is the requirements doc's T1, and its answer to open question 3 ("every subagent, or
# only substantial ones?"): every subagent, but a candidate only when the report says
# something. A trivial report -- "Waiting for the completion event" -- carries no signal
# and so produces nothing, without needing a length rule to say so.
MIN_REPORT_CHARS = 400


def from_report(report: str) -> list[Signal]:
    """Candidates in a subagent's returned report."""
    if len(report) < MIN_REPORT_CHARS or not _TECHNICAL_RE.search(report):
        return []
    events = [Event(kind=TEXT, text=report)]
    return surprises(events) + measured_bounds(events)


def detect(events: list[Event], rules: tuple[str, ...] = DEFAULT_RULES) -> list[Signal]:
    """Every signal from the selected rules, in a stable order."""
    out: list[Signal] = []
    for name in rules:
        rule = _RULES.get(name)
        if rule is not None:
            out.extend(rule(events))
    return out
