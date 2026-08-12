import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "scripts" / "session-start.sh"


def run_script(home):
    env = dict(os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
    return subprocess.run(
        ["bash", str(SCRIPT)], input="{}", capture_output=True, text=True, env=env
    )


def test_emits_hook_specific_output(tmp_path):
    home = tmp_path / "home"
    result = run_script(home)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    inner = payload["hookSpecificOutput"]
    assert inner["hookEventName"] == "SessionStart"
    assert "mneme noticing" in inner["additionalContext"]
    assert "Registered knowledge plugins" in inner["additionalContext"]


def test_exit_zero_when_mneme_broken(tmp_path):
    env = dict(
        os.environ,
        MNEME_HOME=str(tmp_path / "h"),
        CLAUDE_PLUGIN_ROOT=str(tmp_path / "not-a-plugin-root"),
    )
    result = subprocess.run(
        ["bash", str(SCRIPT)], input="{}", capture_output=True, text=True, env=env
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
