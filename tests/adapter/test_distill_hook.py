import json
import os
import stat
import subprocess
from pathlib import Path

from mneme_core import flags, paths, staging

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "scripts" / "distill-hook.sh"

PROPOSALS = {
    "proposals": [
        {
            "type": "fact", "edit": "new", "target": "unassigned", "topic": "hook-e2e",
            "category": "gotcha", "text": "Distilled through the hook pipeline",
            "tags": ["e2e"], "confidence": 0.9, "rationale": "verified in session",
        }
    ]
}


def make_claude_shim(tmp_path):
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "claude"
    result_doc = json.dumps({"result": json.dumps(PROPOSALS)})
    shim.write_text(f"#!/bin/sh\necho '{result_doc}'\n", encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return shim


def run_hook(tmp_path, home, payload, extra_env=None):
    env = dict(
        os.environ,
        MNEME_HOME=str(home),
        CLAUDE_PLUGIN_ROOT=str(REPO_ROOT),
        MNEME_CLAUDE_BIN=str(make_claude_shim(tmp_path)),
        MNEME_DISTILL_FOREGROUND="1",
    )
    env.pop("MNEME_DISTILLING", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )


def test_full_pipeline_stages_candidate(tmp_path):
    home = tmp_path / "home"
    flags.add_flag(home, "solved something hard", session="s1")
    result = run_hook(tmp_path, home, {"transcript_path": "/tmp/t.jsonl", "stop_hook_active": False})
    assert result.returncode == 0
    cands = staging.load_candidates(home)
    assert len(cands) == 1
    assert "Distilled through the hook pipeline" in cands[0].body
    assert flags.read_flags(home) == []  # --clear-flags consumed them


def test_no_flags_no_work(tmp_path):
    home = tmp_path / "home"
    result = run_hook(tmp_path, home, {"transcript_path": "/tmp/t.jsonl"})
    assert result.returncode == 0
    assert staging.load_candidates(home) == []


def test_stop_hook_active_guard(tmp_path):
    home = tmp_path / "home"
    flags.add_flag(home, "x")
    result = run_hook(
        tmp_path, home, {"transcript_path": "/tmp/t.jsonl", "stop_hook_active": True}
    )
    assert result.returncode == 0
    assert staging.load_candidates(home) == []
    assert flags.read_flags(home) != []  # untouched


def test_recursion_guard(tmp_path):
    home = tmp_path / "home"
    flags.add_flag(home, "x")
    result = run_hook(
        tmp_path, home, {"transcript_path": "/t"}, extra_env={"MNEME_DISTILLING": "1"}
    )
    assert result.returncode == 0
    assert staging.load_candidates(home) == []


def test_garbage_payload_is_silent(tmp_path):
    home = tmp_path / "home"
    env = dict(
        os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT),
        MNEME_DISTILL_FOREGROUND="1",
    )
    result = subprocess.run(
        ["bash", str(SCRIPT)], input="not json", capture_output=True, text=True, env=env
    )
    assert result.returncode == 0


def test_corrupt_flag_line_does_not_disable_distillation(tmp_path):
    # `distill pending` is the hook's only gate. One truncated line in flags.jsonl
    # used to make it exit 1 with a traceback, which the hook reads as "nothing
    # pending" — no distill ever ran again while the bad line sat there.
    home = tmp_path / "home"
    flags.add_flag(home, "worth keeping")
    with paths.flags_path(home).open("a", encoding="utf-8") as f:
        f.write("this line is corrupt {\n")
    result = run_hook(tmp_path, home, {"transcript_path": "/tmp/t.jsonl"})
    assert result.returncode == 0
    assert len(staging.load_candidates(home)) == 1
    assert flags.read_flags(home) == []  # the good flag was consumed


def transcript_with(tmp_path, n_tool_calls):
    lines = []
    for i in range(n_tool_calls):
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash",
                                     "id": f"t{i}", "input": {"command": "x"}}]},
        }))
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_the_tally_is_recorded_even_when_nothing_was_captured(tmp_path):
    """The whole point of R3. This hook's next line is

        "$ROOT/bin/mneme" distill pending >/dev/null 2>&1 || exit 0

    which exits 1 — and so exits the hook — precisely when the flag count is zero. The
    session that captured nothing is the one worth recording, so the tally has to happen
    before it.
    """
    from mneme_core import tally

    home = tmp_path / "home"
    t = transcript_with(tmp_path, 30)
    result = run_hook(
        tmp_path, home,
        {"transcript_path": str(t), "session_id": "quiet-sess", "stop_hook_active": False},
    )
    assert result.returncode == 0
    got = tally.read_tallies(home)
    assert len(got) == 1
    assert got[0].session == "quiet-sess"
    assert got[0].tool_calls == 30
    assert got[0].flags == 0
    assert tally.last_notable(home) is not None, "a 30-call session with no flags is notable"


def test_the_tally_uses_the_payloads_session_id(tmp_path):
    from mneme_core import tally

    home = tmp_path / "home"
    t = transcript_with(tmp_path, 5)
    run_hook(tmp_path, home,
             {"transcript_path": str(t), "session_id": "from-payload"})
    assert tally.read_tallies(home)[0].session == "from-payload"


def test_a_payload_without_a_session_id_is_not_tallied_under_the_transcript_path(tmp_path):
    """The tab-split trap: `${FIELDS#*$TAB}` returns the whole string when there is no
    separator, which would file every session under a path-shaped id."""
    from mneme_core import tally

    home = tmp_path / "home"
    t = transcript_with(tmp_path, 5)
    run_hook(tmp_path, home, {"transcript_path": str(t)})
    recorded = tally.read_tallies(home)
    assert len(recorded) == 1
    assert str(t) not in recorded[0].session


def test_the_distillers_own_session_is_not_tallied(tmp_path):
    """MNEME_DISTILLING marks the nested `claude -p` the pipeline runs. Its Stop hook must
    not record a tally — it is mneme's own machinery, not the user's session."""
    from mneme_core import tally

    home = tmp_path / "home"
    t = transcript_with(tmp_path, 40)
    result = run_hook(
        tmp_path, home, {"transcript_path": str(t), "session_id": "inner"},
        extra_env={"MNEME_DISTILLING": "1"},
    )
    assert result.returncode == 0
    assert tally.read_tallies(home) == []
