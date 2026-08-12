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
