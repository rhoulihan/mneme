"""The librarian loop end to end, through the real entry points (spec §7.7).

This test is the scripted stand-in for the in-session agent: `/mneme:classify` reads the
bundle, proposes a mapping, and edits files in the working tree — here those edits are
written by the test instead of by a model, so everything around them (the rails, the
migration, the gates, the PR-only delivery) is exercised for real.
"""
import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROPOSALS = {
    "proposals": [
        {
            "type": "skill", "edit": "new", "target": "classify-e2e",
            "name": "deploy-widget",
            "description": "Use when deploying the widget service after a failed cutover",
            "procedure": "1. Run preflight.\n2. Blue-green cutover.",
            "failure_pattern": "Naive restart loops forever; the LB caches the dead target.",
            "confidence": 0.9, "rationale": "verified this session",
        },
        {
            "type": "fact", "edit": "new", "target": "classify-e2e", "topic": "deploys",
            "category": "gotcha",
            "text": "Widget deploys fail when the load balancer caches dead targets",
            "tags": ["deploy"], "confidence": 0.8, "rationale": "verified this session",
        },
    ]
}


def sh(env, *args, cwd=None):
    return subprocess.run(
        list(args), capture_output=True, text=True, env=env,
        cwd=str(cwd) if cwd else None,
    )


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_classify_loop(tmp_path):
    home = tmp_path / "home"
    env = dict(os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
    env.pop("MNEME_DISTILLING", None)
    mneme = str(REPO_ROOT / "bin" / "mneme")

    # 1. scaffold + register, then wire a bare remote and publish main
    r = sh(env, mneme, "new", "classify-e2e", "--owner", "e2e-team")
    assert r.returncode == 0, r.stderr
    kb = Path(
        json.loads((home / "registry.json").read_text(encoding="utf-8"))["plugins"][0]["path"]
    )
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    git(kb, "remote", "add", "origin", str(remote))
    git(kb, "push", "-u", "origin", "main")

    # 2. an accepted PR's worth of knowledge: one skill and one fact, harvested and merged
    proposals = tmp_path / "proposals.json"
    proposals.write_text(json.dumps(PROPOSALS), encoding="utf-8")
    r = sh(env, mneme, "distill", "ingest", str(proposals), "--source", "e2e")
    assert r.returncode == 0, r.stderr
    # `share list` groups by target: the plugin heading is flush left, candidates indented.
    ids = [
        line.split()[0]
        for line in sh(env, mneme, "share", "list").stdout.splitlines()
        if line.startswith("  ")
    ]
    assert len(ids) == 2, ids
    r = sh(env, mneme, "share", "apply", "--ids", ",".join(ids))
    assert r.returncode == 0, r.stderr
    harvest_branch = next(
        b.strip("* ").strip()
        for b in git(kb, "branch").splitlines()
        if "mneme/harvest-" in b
    )
    git(kb, "merge", harvest_branch)
    git(kb, "push", "origin", "main")

    fact_file = kb / "skills" / "knowledge-index" / "facts" / "deploys.md"
    index_md = kb / "skills" / "knowledge-index" / "SKILL.md"
    assert fact_file.exists()
    assert "| deploys |" in index_md.read_text(encoding="utf-8")

    # 3. the rails: begin on the current directory (no plugin name anywhere)
    r = sh(env, mneme, "classify", "begin", "--cwd", str(kb / "skills"))
    assert r.returncode == 0, r.stderr
    branch = r.stdout.strip()
    assert branch.startswith("mneme/classify-")
    assert git(kb, "rev-parse", "--abbrev-ref", "HEAD") == branch

    # 4. the bundle the in-session librarian reads
    r = sh(env, mneme, "classify", "prepare", "--cwd", str(kb))
    assert r.returncode == 0, r.stderr
    bundle = json.loads(r.stdout)
    assert bundle["plugin"] == "classify-e2e"
    assert bundle["legacy_layout"] is False
    assert len(bundle["facts"]) == 1, bundle["facts"]
    assert bundle["facts"][0]["unit_id"].startswith("facts/deploys#")
    assert bundle["facts"][0]["category"] == "gotcha"
    assert "deploy-widget" in [s["name"] for s in bundle["skills"]]
    assert "knowledge-index" not in [s["name"] for s in bundle["skills"]]
    assert "LIBRARIAN" in bundle["instructions"]

    # 5. the scripted agent: integrate the fact into the skill it belongs to, then the
    #    fact file has no bullets left, so it goes.
    fact_text = bundle["facts"][0]["text"]
    verified = bundle["facts"][0]["verified"]
    skill_md = kb / "skills" / "deploy-widget" / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8")
        + f"\n## Operational notes\n\n- {fact_text} (verified: {verified})\n",
        encoding="utf-8",
    )
    fact_file.unlink()

    # 6. finalize: gates, commit, push, PR — and back to an untouched main
    main_before = git(kb, "rev-parse", "main")
    r = sh(env, mneme, "classify", "finalize", "--cwd", str(kb))
    assert r.returncode == 0, r.stderr
    assert f"on {branch}" in r.stdout
    assert "pr:" in r.stdout

    # 7. PR-only delivery: the branch reached the remote, main moved nowhere
    assert branch in git(remote, "branch")
    assert git(kb, "rev-parse", "main") == main_before
    assert git(remote, "rev-parse", "main") == main_before
    assert git(kb, "rev-parse", f"origin/{branch}") == git(kb, "rev-parse", branch)
    assert git(kb, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(kb, "status", "--porcelain") == ""
    assert git(kb, "log", branch, "-1", "--format=%s").startswith("knowledge: classify")

    # 8. on the branch: the fact is filed in the skill and the index no longer routes to it
    tree = git(kb, "ls-tree", "-r", "--name-only", branch).splitlines()
    assert "skills/knowledge-index/facts/deploys.md" not in tree
    assert "skills/deploy-widget/SKILL.md" in tree
    branch_index = git(kb, "show", f"{branch}:skills/knowledge-index/SKILL.md")
    assert "| deploys |" not in branch_index
    assert fact_text in git(kb, "show", f"{branch}:skills/deploy-widget/SKILL.md")

    # 9. once the PR is accepted, retrieval finds the knowledge through the skill
    git(kb, "merge", branch)
    assert sh(env, mneme, "index", "rebuild").returncode == 0
    r = sh(env, mneme, "search", "load balancer caches dead targets")
    assert r.returncode == 0, r.stderr
    assert "skills/deploy-widget" in r.stdout
    assert "facts/deploys" not in r.stdout
