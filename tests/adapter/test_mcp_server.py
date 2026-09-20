"""The zero-friction flag path (R6).

In the session that motivated automatic capture, several flags contained `$`, `"` and
backslashes needing escape, and one batch failed outright on quoting. A capture path that
costs a carefully-quoted shell invocation competes with real work and loses.

Hand-rolled because mneme is stdlib-only and its release tests enforce that — MCP's stdio
transport is newline-delimited JSON-RPC 2.0, which is small enough to implement exactly.
"""
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from mneme_core import flags, mcp_server

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "bin" / "mneme-mcp"


def converse(home, *messages):
    """Drive the server over an in-memory pipe, as a client would."""
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    stdout = io.StringIO()
    mcp_server.serve(home, stdin=stdin, stdout=stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def req(rid, method, **params):
    m = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params:
        m["params"] = params
    return m


def flag_call(rid, **arguments):
    return req(rid, "tools/call", name="mneme_flag", arguments=arguments)


# --- protocol ----------------------------------------------------------------


def test_the_handshake_answers_with_a_server_and_capabilities(tmp_path):
    out = converse(tmp_path / "home", req(1, "initialize",
                                          protocolVersion="2025-06-18", capabilities={}))
    assert len(out) == 1
    result = out[0]["result"]
    assert out[0]["jsonrpc"] == "2.0" and out[0]["id"] == 1
    assert result["serverInfo"]["name"] == "mneme"
    assert result["protocolVersion"] == "2025-06-18"
    assert "tools" in result["capabilities"]


def test_an_older_protocol_the_server_knows_is_honoured(tmp_path):
    """Echoing the client's version back is how a client learns the session is on it."""
    out = converse(tmp_path / "home", req(1, "initialize", protocolVersion="2024-11-05"))
    assert out[0]["result"]["protocolVersion"] == "2024-11-05"


def test_an_unknown_protocol_falls_back_to_one_we_speak(tmp_path):
    out = converse(tmp_path / "home", req(1, "initialize", protocolVersion="1999-01-01"))
    assert out[0]["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION


def test_a_notification_is_never_answered(tmp_path):
    """A JSON-RPC notification has no id; replying to one is a protocol violation."""
    out = converse(tmp_path / "home", {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert out == []


def test_an_unknown_method_is_a_json_rpc_error(tmp_path):
    out = converse(tmp_path / "home", req(9, "resources/list"))
    assert out[0]["error"]["code"] == -32601


def test_ping_is_answered(tmp_path):
    assert converse(tmp_path / "home", req(4, "ping"))[0]["result"] == {}


def test_the_tool_is_advertised_with_a_usable_schema(tmp_path):
    tools = converse(tmp_path / "home", req(2, "tools/list"))[0]["result"]["tools"]
    assert [t["name"] for t in tools] == ["mneme_flag"]
    schema = tools[0]["inputSchema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["text"]
    assert set(schema["properties"]["kind"]["enum"]) == set(flags.KINDS)
    assert tools[0]["description"].strip()


# --- the tool ----------------------------------------------------------------


def test_a_flag_is_recorded(tmp_path):
    home = tmp_path / "home"
    out = converse(home, flag_call(3, text="The webhook replays for 72 hours"))
    # A success carries no `isError` at all — asserting "False if present" would pass
    # trivially on a result that never sets it, which is how this assertion was first
    # written and is the exact shape of weak test this repo keeps finding.
    assert "isError" not in out[0]["result"]
    assert "flagged" in out[0]["result"]["content"][0]["text"]
    recorded = flags.read_flags(home)
    assert len(recorded) == 1
    assert recorded[0]["text"] == "The webhook replays for 72 hours"
    assert recorded[0]["kind"] == "golden-path"


def test_the_kind_is_honoured(tmp_path):
    home = tmp_path / "home"
    converse(home, flag_call(3, text="installed knowledge is stale",
                             kind="knowledge-issue"))
    assert flags.read_flags(home)[0]["kind"] == "knowledge-issue"


def test_shell_metacharacters_need_no_escaping(tmp_path):
    """Acceptance criterion 4, and the entire reason this tool exists."""
    home = tmp_path / "home"
    nasty = 'Backticks in a $(double-quoted) "git commit -m" still expand — use \'single\' or \\a heredoc'
    converse(home, flag_call(3, text=nasty))
    assert flags.read_flags(home)[0]["text"] == nasty


def test_a_newline_is_accepted_and_flattened(tmp_path):
    """The caller may send one — criterion 4 says so. mneme's own rule is one line per
    flag, so it is collapsed here rather than refused: a tool caller cannot be asked to
    pre-flatten, and refusing would hand back exactly the friction this replaces."""
    home = tmp_path / "home"
    converse(home, flag_call(3, text="first part\nsecond part"))
    text = flags.read_flags(home)[0]["text"]
    assert "\n" not in text
    assert text == "first part second part"


def test_an_enormous_flag_is_capped(tmp_path):
    home = tmp_path / "home"
    converse(home, flag_call(3, text="x" * 50_000))
    assert len(flags.read_flags(home)[0]["text"]) == mcp_server.MAX_FLAG_TEXT


@pytest.mark.parametrize("arguments", [{}, {"text": ""}, {"text": "   "}, {"text": 42}])
def test_an_unusable_flag_is_a_result_not_a_crash(tmp_path, arguments):
    home = tmp_path / "home"
    out = converse(home, flag_call(3, **arguments))
    assert out[0]["result"]["isError"] is True
    assert "error" not in out[0], "a refusal must not read as a broken transport"
    # The message is the tool's own, not whatever `flags.add_flag` happens to raise: the
    # caller is a model that has to correct itself from this sentence alone.
    assert "non-empty string" in out[0]["result"]["content"][0]["text"]
    assert flags.read_flags(home) == []


def test_an_invalid_kind_is_refused_without_writing(tmp_path):
    home = tmp_path / "home"
    out = converse(home, flag_call(3, text="a thing", kind="not-a-kind"))
    assert out[0]["result"]["isError"] is True
    assert flags.read_flags(home) == []


def test_an_unknown_tool_is_a_result_not_a_transport_error(tmp_path):
    """A transport error reads as "the server is broken" and stops the client trying
    again; an isError result is something the model can correct."""
    home = tmp_path / "home"
    # Valid arguments on purpose: with `arguments={}` the empty-text guard produces the
    # error and the tool-NAME check is never reached, so the test would pass against a
    # server that happily flagged for any tool name at all.
    out = converse(home, req(3, "tools/call", name="mneme_delete_everything",
                             arguments={"text": "a perfectly good line"}))
    assert out[0]["result"]["isError"] is True
    assert "error" not in out[0]
    assert "mneme_delete_everything" in out[0]["result"]["content"][0]["text"]
    assert flags.read_flags(home) == [], "an unknown tool name wrote a flag"


# --- robustness: stdout is the protocol --------------------------------------


def test_a_malformed_line_is_skipped_and_the_server_continues(tmp_path):
    home = tmp_path / "home"
    stdin = io.StringIO(
        "not json\n"
        + json.dumps([1, 2, 3]) + "\n"
        + "\n"
        + json.dumps(flag_call(5, text="still working")) + "\n"
    )
    stdout = io.StringIO()
    mcp_server.serve(home, stdin=stdin, stdout=stdout)
    lines = [l for l in stdout.getvalue().splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == 5
    assert len(flags.read_flags(home)) == 1


def test_every_stdout_line_is_a_json_rpc_message(tmp_path):
    """stdout IS the protocol. One stray print corrupts the stream for the whole session,
    and it is the one mistake a change here can make silently."""
    home = tmp_path / "home"
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in (
        req(1, "initialize", protocolVersion="2025-06-18"),
        req(2, "tools/list"),
        flag_call(3, text="a thing worth keeping"),
        req(4, "ping"),
        req(5, "nonsense/method"),
    )))
    stdout = io.StringIO()
    mcp_server.serve(home, stdin=stdin, stdout=stdout)
    for line in stdout.getvalue().splitlines():
        if not line.strip():
            continue
        message = json.loads(line)
        assert message["jsonrpc"] == "2.0"
        assert "id" in message
        assert ("result" in message) ^ ("error" in message)


# --- through the shipped launcher --------------------------------------------


def test_the_launcher_speaks_the_protocol(tmp_path):
    home = tmp_path / "home"
    env = dict(os.environ, MNEME_HOME=str(home))
    payload = "".join(json.dumps(m) + "\n" for m in (
        req(1, "initialize", protocolVersion="2025-06-18"),
        flag_call(2, text="through the real binary"),
    ))
    result = subprocess.run([str(LAUNCHER)], input=payload, capture_output=True,
                            text=True, env=env, timeout=60)
    assert result.returncode == 0
    lines = [json.loads(l) for l in result.stdout.splitlines() if l.strip()]
    assert lines[0]["result"]["serverInfo"]["name"] == "mneme"
    assert len(flags.read_flags(home)) == 1


def test_the_plugin_manifest_declares_the_server():
    manifest = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    server = manifest["mcpServers"]["mneme"]
    assert server["command"].endswith("/bin/mneme-mcp")
    assert "${CLAUDE_PLUGIN_ROOT}" in server["command"]
