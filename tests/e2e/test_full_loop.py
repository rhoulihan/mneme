"""The whole loop, through the real entry points (spec §7 end to end)."""
import json
import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROPOSALS = {
    "proposals": [
        {
            "type": "skill", "edit": "new", "target": "e2e-knowledge",
            "name": "deploy-widget", "description": "Use when deploying the widget service after a failed cutover",
            "procedure": "1. Run preflight.\n2. Blue-green cutover.",
            "failure_pattern": "Naive restart loops forever; the LB caches the dead target.",
            "confidence": 0.9, "rationale": "verified this session",
        },
        {
            "type": "fact", "edit": "new", "target": "e2e-knowledge", "topic": "staging-env",
            "category": "gotcha", "text": "The staging key is AKIAIOSFODNN7EXAMPLE",
            "tags": ["staging"], "confidence": 0.4, "rationale": "seen once",
        },
    ]
}


def sh(env, *args, stdin=None, cwd=None):
    return subprocess.run(
        list(args), input=stdin, capture_output=True, text=True, env=env,
        cwd=str(cwd) if cwd else None,
    )


def test_full_loop(tmp_path):
    home = tmp_path / "home"
    env = dict(
        os.environ,
        MNEME_HOME=str(home),
        CLAUDE_PLUGIN_ROOT=str(REPO_ROOT),
        MNEME_DISTILL_FOREGROUND="1",
    )
    env.pop("MNEME_DISTILLING", None)
    mneme = str(REPO_ROOT / "bin" / "mneme")

    # claude shim: returns the canned distiller output. The payload is catted from
    # a file rather than echoed inline: /bin/sh's echo eats backslash escapes, which
    # would turn the proposals' \n into a raw newline and hand ingest invalid JSON.
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    canned = bindir / "canned.json"
    canned.write_text(json.dumps({"result": json.dumps(PROPOSALS)}), encoding="utf-8")
    shim = bindir / "claude"
    shim.write_text('#!/bin/sh\ncat "%s"\n' % canned, encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    env["MNEME_CLAUDE_BIN"] = str(shim)

    # 1. scaffold + register
    r = sh(env, mneme, "new", "e2e-knowledge", "--owner", "e2e-team")
    assert r.returncode == 0, r.stderr
    kb = json.loads((home / "registry.json").read_text(encoding="utf-8"))["plugins"][0]["path"]

    # 2. wire a bare remote
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "-C", kb, "remote", "add", "origin", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "-C", kb, "push", "-u", "origin", "main"], check=True, capture_output=True)

    # 3. session-start hook injects the brief
    r = sh(env, "bash", str(REPO_ROOT / "hooks" / "scripts" / "session-start.sh"), stdin="{}")
    assert r.returncode == 0
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "e2e-knowledge" in ctx

    # 4. flag, then the Stop hook distills through the shim
    assert sh(env, mneme, "flag", "solved the widget cutover after two dead ends").returncode == 0
    r = sh(
        env, "bash", str(REPO_ROOT / "hooks" / "scripts" / "distill-hook.sh"),
        stdin=json.dumps({"transcript_path": "/tmp/e2e.jsonl", "stop_hook_active": False}),
    )
    assert r.returncode == 0

    # 5. machine gate: clean skill staged, secret-bearing fact quarantined, flags consumed
    r = sh(env, mneme, "stage", "list", "--all")
    assert "skill/new" in r.stdout and "staged" in r.stdout
    assert "quarantined" in r.stdout
    assert sh(env, mneme, "distill", "pending").returncode == 1

    # 6. human gate: approve the staged skill only
    staged_id = next(
        line.split()[0]
        for line in sh(env, mneme, "stage", "list").stdout.splitlines()
        if line.strip()
    )
    r = sh(env, mneme, "share", "apply", "--ids", staged_id)
    assert r.returncode == 0, r.stderr
    assert "harvested e2e-knowledge: 1 units" in r.stdout

    # 7. the harvest branch reached the remote with provenance
    branches = subprocess.run(
        ["git", "-C", str(remote), "branch"], capture_output=True, text=True, check=True
    ).stdout
    harvest_branch = next(b.strip() for b in branches.splitlines() if "mneme/harvest-" in b)
    message = subprocess.run(
        ["git", "-C", str(remote), "log", harvest_branch, "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "knowledge: harvest" in message
    assert "Mneme-Source:" in message

    # 8. staging drained (quarantined item remains, staged one gone)
    assert sh(env, mneme, "stage", "list").stdout.strip() == ""

    # 9. status reflects the run
    out = sh(env, mneme, "status").stdout
    assert "plugins: 1 registered" in out
    assert "submissions: 1 recorded" in out

    # 10. merge the harvest into the kb main and confirm retrieval finds it
    subprocess.run(["git", "-C", kb, "merge", harvest_branch.strip()], check=True, capture_output=True)
    assert sh(env, mneme, "index", "rebuild").returncode == 0
    r = sh(env, mneme, "search", "cutover widget")
    assert "skills/deploy-widget" in r.stdout
