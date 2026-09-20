"""T1 — mining the report a delegated agent just returned.

The requirements doc's highest-value trigger: "in the observed session, essentially every
durable fact arrived this way." A probe against Claude Code 2.1.278 settled its shape:
`SubagentStop` cannot inject context into the parent, but the report arrives in its payload
as `last_assistant_message`. It can see; it cannot speak. So it records and
`UserPromptSubmit` delivers.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from mneme_core import detect, noticed

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "scripts" / "subagent-stop.sh"

FINDING = (
    "I traced the failure to the container shim. The SQLcl container shim silently "
    "mis-splits arguments containing `=`, so `--set foo=bar` arrives as two tokens and "
    "the connection is opened against the wrong service. It turns out the 8000 byte "
    "inline limit is what pushes the payload over. " + "Detail follows. " * 20
)


def run_hook(home, payload, extra_env=None):
    env = dict(os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
    env.pop("MNEME_DISTILLING", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(SCRIPT)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60)


def test_a_finding_in_a_report_becomes_a_candidate(tmp_path):
    home = tmp_path / "home"
    result = run_hook(home, {
        "session_id": "s1", "agent_type": "general-purpose",
        "last_assistant_message": FINDING,
    })
    assert result.returncode == 0
    held = noticed.read_noticed(home)
    assert held, "a substantive report produced no candidate"
    assert any("general-purpose" in e.detail for e in held)


def test_the_candidate_reaches_the_user_at_the_next_prompt(tmp_path):
    """The two halves of T1: SubagentStop sees, UserPromptSubmit speaks."""
    home = tmp_path / "home"
    run_hook(home, {"session_id": "s1", "agent_type": "explorer",
                    "last_assistant_message": FINDING})
    deliver = REPO_ROOT / "hooks" / "scripts" / "user-prompt-submit.sh"
    env = dict(os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
    env.pop("MNEME_DISTILLING", None)
    out = subprocess.run(["bash", str(deliver)], input='{"session_id":"s1"}',
                         capture_output=True, text=True, env=env)
    context = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "explorer" in context


def test_a_trivial_report_produces_nothing(tmp_path):
    """Measured over 787 real reports: 60 are trivial ("Waiting for the completion
    event"). They carry no signal, so they need no length rule to exclude them — but the
    floor is there so a one-line acknowledgement cannot cost a prompt."""
    home = tmp_path / "home"
    for trivial in ("Done.", "179/180 — one cell left. Waiting for the completion event.",
                    "[Request interrupted by user for tool use]"):
        run_hook(home, {"session_id": "s1", "last_assistant_message": trivial})
    assert noticed.read_noticed(home) == []


def test_a_long_report_that_says_nothing_produces_nothing(tmp_path):
    """Length alone is not the test — N4 says a session producing no findings produces no
    noise, and most reports are long."""
    home = tmp_path / "home"
    run_hook(home, {"session_id": "s1",
                    "last_assistant_message": "I read the files you asked about. " * 60})
    assert noticed.read_noticed(home) == []


def test_the_same_report_twice_is_one_candidate(tmp_path):
    home = tmp_path / "home"
    run_hook(home, {"session_id": "s1", "last_assistant_message": FINDING})
    first = len(noticed.read_noticed(home))
    run_hook(home, {"session_id": "s1", "last_assistant_message": FINDING})
    assert len(noticed.read_noticed(home)) == first


def test_a_very_large_report_is_handled(tmp_path):
    """60KB reports exist in the wild; the payload goes through a file, not an argument."""
    home = tmp_path / "home"
    result = run_hook(home, {"session_id": "s1",
                             "last_assistant_message": FINDING + "x" * 200_000})
    assert result.returncode == 0
    for e in noticed.read_noticed(home):
        assert len(e.evidence) <= noticed.MAX_FIELD


def test_the_distillers_own_subagents_are_ignored(tmp_path):
    home = tmp_path / "home"
    result = run_hook(home, {"session_id": "s1", "last_assistant_message": FINDING},
                      extra_env={"MNEME_DISTILLING": "1"})
    assert result.returncode == 0
    assert noticed.read_noticed(home) == []


@pytest.mark.parametrize("payload", [{}, {"session_id": ""},
                                     {"session_id": "s1"},
                                     {"session_id": "s1", "last_assistant_message": ""}])
def test_an_incomplete_payload_is_silent_not_broken(tmp_path, payload):
    home = tmp_path / "home"
    result = run_hook(home, payload)
    assert result.returncode == 0
    assert noticed.read_noticed(home) == []


def test_garbage_on_stdin_exits_zero(tmp_path):
    home = tmp_path / "home"
    env = dict(os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
    env.pop("MNEME_DISTILLING", None)
    for junk in ("not json", "", "[1,2,3]", "null"):
        r = subprocess.run(["bash", str(SCRIPT)], input=junk, capture_output=True,
                           text=True, env=env)
        assert r.returncode == 0, junk


def test_the_report_rules_are_scoped_to_reports():
    """The same prose rules are OFF for whole-session transcripts, where they are dominated
    by status reporting. The base rate is the difference, and it is measured: 29% of
    substantive subagent reports carry a signal."""
    assert "surprise" not in detect.DEFAULT_RULES
    assert detect.from_report(FINDING)


def test_the_candidate_names_the_finding_not_the_trigger_word(tmp_path):
    """R2's standard: a generic "flag anything useful?" is dismissed. `surprises` puts its
    matched keyword in `detail`, so rendering that produced
    "from the general-purpose report: silently" — which names nothing at all."""
    home = tmp_path / "home"
    run_hook(home, {"session_id": "s1", "agent_type": "general-purpose",
                    "last_assistant_message": FINDING})
    details = [e.detail for e in noticed.read_noticed(home)]
    assert details
    assert all(len(d) > 40 for d in details), details
    assert any("shim" in d or "8000" in d for d in details), details


@pytest.mark.parametrize("status", [
    "Report delivered. All Round-3 corrections are applied to the memo in place "
    "(891 to 1,269 lines), with seven judgement calls recorded.",
    "Full suite is running in the background (~20 min, teed to /tmp/suite.log). "
    "All four commits are in place; nothing else to report.",
])
def test_a_short_status_update_is_not_a_finding(tmp_path, status):
    """Both of these are real short reports from this machine, and both trip the
    `measured` rule on their own numbers — "891 to 1,269 lines", "~20 min". They are
    progress notes, not findings, and the length floor is what separates them.

    Measured: of 60 real sub-400-char reports, exactly these 2 carry a signal, and neither
    is durable knowledge. The floor earns its place on that evidence, not on a guess.
    """
    home = tmp_path / "home"
    assert len(status) < detect.MIN_REPORT_CHARS
    run_hook(home, {"session_id": "s1", "last_assistant_message": status})
    assert noticed.read_noticed(home) == []


def test_the_floor_is_pinned_to_its_value():
    """A threshold read from the constant cannot be pinned by a test that also reads it."""
    assert detect.MIN_REPORT_CHARS == 400
