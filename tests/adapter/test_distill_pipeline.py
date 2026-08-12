"""The distiller runs for minutes while the session keeps going (spec §7.2).

Whatever is flagged in that window must still be pending when the pipeline lands.
"""
import json
import os
import stat
import subprocess
import time
from pathlib import Path

from mneme_core import flags, staging

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE = REPO_ROOT / "bin" / "mneme-distill-pipeline"

PROPOSALS = {
    "proposals": [
        {
            "type": "fact", "edit": "new", "target": "unassigned", "topic": "race",
            "category": "gotcha", "text": "Distilled while the session kept flagging",
            "tags": ["race"], "confidence": 0.9, "rationale": "verified in session",
        }
    ]
}


def gated_claude_shim(tmp_path, started, go):
    """A `claude` that blocks until the test releases it — a slow distiller."""
    shim = tmp_path / "claude"
    result_doc = json.dumps({"result": json.dumps(PROPOSALS)})
    shim.write_text(
        "#!/bin/sh\n"
        f"touch '{started}'\n"
        f"while [ ! -f '{go}' ]; do sleep 0.05; done\n"
        f"echo '{result_doc}'\n",
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return shim


def wait_for(path, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def run_pipeline(tmp_path, home, shim):
    env = dict(os.environ, MNEME_HOME=str(home), MNEME_CLAUDE_BIN=str(shim))
    env.pop("MNEME_DISTILLING", None)
    return subprocess.Popen(
        [str(PIPELINE), "/tmp/t.jsonl"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )


def test_flag_captured_mid_run_is_not_destroyed(tmp_path):
    # PreCompact fires mid-session and the session continues: every /mneme:capture
    # between prepare and ingest used to be unlinked with the whole flags file,
    # having never reached a distiller.
    home = tmp_path / "home"
    flags.add_flag(home, "flagged before the distiller started", session="s1")
    started, go = tmp_path / "started", tmp_path / "go"
    proc = run_pipeline(tmp_path, home, gated_claude_shim(tmp_path, started, go))
    try:
        wait_for(started)
        mid_run = flags.add_flag(home, "flagged while the distiller ran", session="s1")
        go.touch()
        _out, err = proc.communicate(timeout=120)
    finally:
        if proc.poll() is None:  # pragma: no cover - only on a hung pipeline
            proc.kill()
            proc.communicate()
    assert proc.returncode == 0, err
    cands = staging.load_candidates(home)
    assert len(cands) == 1
    assert "Distilled while the session kept flagging" in cands[0].body
    assert flags.read_flags(home) == [mid_run]


def test_pipeline_consumes_the_flags_it_distilled(tmp_path):
    home = tmp_path / "home"
    flags.add_flag(home, "flagged before the distiller started", session="s1")
    go = tmp_path / "go"
    go.touch()
    proc = run_pipeline(tmp_path, home, gated_claude_shim(tmp_path, tmp_path / "s", go))
    _out, err = proc.communicate(timeout=120)
    assert proc.returncode == 0, err
    assert len(staging.load_candidates(home)) == 1
    assert flags.read_flags(home) == []
