"""A minimal MCP server over stdio — the zero-friction flag path (R6).

Why this exists: in the session that motivated automatic capture, several flags contained
`$`, `"` and backslashes needing escape, and one batch failed outright on quoting. A
capture path that costs a carefully-quoted shell invocation competes with real work and
loses. A tool call does not.

Why it is hand-rolled: mneme is stdlib-only and its release tests enforce that, so there
is no `mcp` package to import. MCP's stdio transport is newline-delimited JSON-RPC 2.0,
which is small enough to implement exactly.

**stdout IS the protocol.** Nothing in this module may print anything that is not a
JSON-RPC message; diagnostics go to stderr. That is the one rule a change here can break
silently and catastrophically.

On safety: the Funes spec's D12 gates a model-invocable path INTO a knowledge repo,
because that would bypass the `disable-model-invocation: true` protecting `/mneme:share`.
`mneme_flag` is categorically different. A flag is a note in MNEME_HOME; the human gate
stays exactly where it was, at share time. So this tool bypasses nothing, and it is the
general-purpose justification for a surface D12 deliberately withheld.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import flags, paths
from .errors import MnemeError

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "mneme"

# One line per flag is mneme's own rule — flags are one-liners and a background distiller
# consolidates later. A tool caller cannot be asked to pre-flatten, so the newline they
# are explicitly allowed to send is collapsed here rather than refused.
MAX_FLAG_TEXT = 2000

TOOLS: list[dict[str, Any]] = [
    {
        "name": "mneme_flag",
        "description": (
            "Record one line of hard-won knowledge for later distillation. Use when a fix"
            " lands after real dead ends, or when installed knowledge proves wrong."
            " Costs nothing and does not interrupt your work: the flag is consolidated by"
            " a background pass and always passes a human gate before it reaches a"
            " knowledge repo. Prefer this over running `mneme flag` in a shell — no"
            " quoting, and text may contain quotes, $, backslashes and newlines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "What was learned and why it was non-obvious. One line.",
                },
                "kind": {
                    "type": "string",
                    "enum": sorted(flags.KINDS),
                    "description": (
                        "'golden-path' for a hard-won fix (default);"
                        " 'knowledge-issue' when installed knowledge is wrong or stale."
                    ),
                },
            },
            "required": ["text"],
        },
    }
]


def _normalise(text: str) -> str:
    """Collapse to one line, bounded. The caller may send anything; the store has a rule."""
    return " ".join(text.split())[:MAX_FLAG_TEXT]


def call_tool(home: Path, name: str, arguments: dict) -> dict:
    if name != "mneme_flag":
        raise MnemeError(f"unknown tool: {name}")
    raw = arguments.get("text", "")
    if not isinstance(raw, str) or not raw.strip():
        raise MnemeError("text must be a non-empty string")
    kind = arguments.get("kind") or "golden-path"
    if not isinstance(kind, str):
        raise MnemeError("kind must be a string")
    cwd = arguments.get("cwd")
    record = flags.add_flag(
        home,
        _normalise(raw),
        kind=kind,
        cwd=Path(cwd) if isinstance(cwd, str) and cwd else None,
    )
    pending = len(flags.read_flags(home))
    return {
        "content": [
            {
                "type": "text",
                "text": f"flagged ({kind}); {pending} pending for the next distill",
            }
        ],
        "structuredContent": {"kind": record["kind"], "pending": pending},
    }


def handle(home: Path, message: dict) -> dict | None:
    """One request in, one response out. `None` means notification — say nothing."""
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None  # a notification; responding to one is a protocol error
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL_VERSION
        return _ok(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": _version()},
        })
    if method == "tools/list":
        return _ok(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            result = call_tool(home, params.get("name", ""),
                               params.get("arguments") or {})
        except MnemeError as e:
            # A refusal the model can act on is a RESULT with isError, not a transport
            # error: the latter reads as "the server is broken" and stops it trying again.
            return _ok(request_id, {
                "content": [{"type": "text", "text": str(e)}], "isError": True,
            })
        except Exception as e:  # never take the client down
            return _ok(request_id, {
                "content": [{"type": "text", "text": f"mneme: {e}"}], "isError": True,
            })
        return _ok(request_id, result)
    if method == "ping":
        return _ok(request_id, {})
    return _error(request_id, -32601, f"method not found: {method}")


def _version() -> str:
    from . import __version__

    return __version__


def _ok(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code,
                                                          "message": message}}


def serve(home: Path, stdin=None, stdout=None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    paths.ensure_layout(home)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            # No id to answer with, so there is nobody to tell. Dropping one malformed
            # line is right; exiting would take the session's tool down with it.
            continue
        if not isinstance(message, dict):
            continue
        response = handle(home, message)
        if response is None:
            continue
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()
    return 0
