"""The only event that fires repeatedly during a session AND can inject context.

Verified against Claude Code 2.1.278 with a live probe: `Stop` cannot inject and its
stdout is discarded; `SubagentStop` can see a subagent's report but cannot speak to the
parent; `UserPromptSubmit` can, via `hookSpecificOutput.additionalContext`.
"""
import json
import os
import subprocess
from pathlib import Path

from mneme_core import detect, noticed

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "scripts" / "user-prompt-submit.sh"


def run_hook(home, payload, extra_env=None):
    env = dict(os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
    env.pop("MNEME_DISTILLING", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )


def a_candidate(home, session="s1"):
    noticed.record_signals(home, session, [detect.Signal(
        kind="resolved-error",
        evidence="ORA-01000: maximum open cursors exceeded",
        detail="ORA-01000 then a success of `sqlplus`, after 4 failures",
    )])


def test_a_pending_candidate_reaches_the_model(tmp_path):
    home = tmp_path / "home"
    a_candidate(home)
    result = run_hook(home, {"session_id": "s1", "prompt": "carry on"})
    assert result.returncode == 0
    inner = json.loads(result.stdout)["hookSpecificOutput"]
    assert inner["hookEventName"] == "UserPromptSubmit"
    assert "ORA-01000" in inner["additionalContext"]


def test_it_is_silent_when_nothing_was_noticed(tmp_path):
    """N4. A session producing no candidates must produce no prompt at all — not an empty
    one, which still costs a turn's attention."""
    home = tmp_path / "home"
    result = run_hook(home, {"session_id": "s1"})
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_the_same_candidate_is_not_offered_twice(tmp_path):
    home = tmp_path / "home"
    a_candidate(home)
    first = run_hook(home, {"session_id": "s1"})
    second = run_hook(home, {"session_id": "s1"})
    assert "ORA-01000" in first.stdout
    assert second.stdout.strip() == ""


def test_another_sessions_candidate_is_not_offered(tmp_path):
    home = tmp_path / "home"
    a_candidate(home, session="other")
    assert run_hook(home, {"session_id": "s1"}).stdout.strip() == ""


def test_the_distillers_own_session_is_not_prompted(tmp_path):
    """The distiller runs headless with --allowedTools "Read,Grep,Glob" and cannot flag
    anything, so a capture prompt there is an instruction it is unable to obey."""
    home = tmp_path / "home"
    a_candidate(home)
    result = run_hook(home, {"session_id": "s1"}, extra_env={"MNEME_DISTILLING": "1"})
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_a_payload_with_no_session_is_silent_not_broken(tmp_path):
    home = tmp_path / "home"
    a_candidate(home)
    for payload in ({}, {"session_id": ""}, {"other": "field"}):
        result = run_hook(home, payload)
        assert result.returncode == 0
        assert result.stdout.strip() == ""


def test_garbage_on_stdin_exits_zero(tmp_path):
    home = tmp_path / "home"
    env = dict(os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
    env.pop("MNEME_DISTILLING", None)
    for junk in ("not json", "", "[1,2,3]", "null"):
        result = subprocess.run(["bash", str(SCRIPT)], input=junk,
                                capture_output=True, text=True, env=env)
        assert result.returncode == 0, junk


def test_a_broken_mneme_does_not_break_the_turn(tmp_path):
    env = dict(os.environ, MNEME_HOME=str(tmp_path / "h"),
               CLAUDE_PLUGIN_ROOT=str(tmp_path / "not-a-plugin-root"))
    result = subprocess.run(["bash", str(SCRIPT)], input='{"session_id":"s1"}',
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0
    assert result.stdout.strip() == ""
