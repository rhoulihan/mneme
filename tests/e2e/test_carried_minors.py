import json
import os
import subprocess
from pathlib import Path

from mneme_core import flags, paths
from mneme_core.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_marketplace_has_description():
    data = json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert data.get("description")


def test_distill_hook_survives_huge_payload(tmp_path):
    home = tmp_path / "home"
    payload = json.dumps(
        {
            "transcript_path": "/tmp/t.jsonl",
            "stop_hook_active": True,
            "last_assistant_message": "x" * 300_000,
        }
    )
    env = dict(
        os.environ,
        MNEME_HOME=str(home),
        CLAUDE_PLUGIN_ROOT=str(REPO_ROOT),
        MNEME_DISTILL_FOREGROUND="1",
    )
    env.pop("MNEME_DISTILLING", None)
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "hooks" / "scripts" / "distill-hook.sh")],
        input=payload, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0
    # guard held despite the size: nothing distilled (stop_hook_active)


def test_status_survives_corrupt_ledgers(tmp_path, capsys):
    home = tmp_path / "home"
    flags.add_flag(home, "good flag")
    with paths.flags_path(home).open("a", encoding="utf-8") as f:
        f.write("{corrupt json\n")
    paths.ensure_layout(home)
    with paths.submitted_path(home).open("a", encoding="utf-8") as f:
        f.write('{"target": "ok-kb", "branch": "b", "units": ["u"]}\n')
        f.write("also not json\n")
    code, out, _ = run(capsys, "--home", str(home), "status")
    assert code == 0
    assert "flags: 1 pending" in out
    assert "submissions: 1 recorded" in out
    assert "unreadable line" in out
