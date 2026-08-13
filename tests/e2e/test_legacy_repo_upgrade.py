"""A pre-canonical knowledge repo upgrades itself, through the real entry points.

This is the 2026-08-12 directive as a user sees it happen. The repo here is not a doctored
0.7 scaffold: it is written out the way mneme v0.2 wrote one — facts at the repo root, a
hand-maintained `knowledge-index` table describing that layout — then registered and used.
The user does nothing but contribute one new fact. Everything else is mneme's doing.

What the story has to be true about, end to end and in this order:

* **The upgrade is a side effect of using the repo.** Nobody runs a migration command here.
  One `share apply` is the whole trigger, and afterwards the branch carries every fact in
  `skills/knowledge-index/facts/` with nothing left at the root.
* **The router still routes.** The regenerated index lists every topic — the two that moved
  and the one that arrived — and each File cell resolves to a file that really exists on
  the branch when read from the skill's own directory. The v0.2 table seeded here is stale
  in a way regeneration must fix (it names a topic the repo no longer has), so a test that
  passes with the regeneration removed is not this test.
* **History moved with the files.** `git log --follow` on a migrated path still reaches the
  commit that seeded it, because a migration that reads as "delete + create" loses the one
  thing a knowledge repo cannot regenerate: who wrote a fact, and when.
* **PR-only survives the upgrade.** `main` is byte-identical afterwards and still legacy;
  the layout changes when a human merges the branch, not before.
* **Retrieval spans the seam.** Once merged, `index rebuild` + `search` find the old facts
  at their new path and the new one beside them — the point of the whole exercise being
  that nothing became unfindable on the way.
"""
import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Spelled out, not imported: these tests are the user-visible contract for where a
# migrated fact ends up, so they must fail if the constant behind it ever moves.
CANON = "skills/knowledge-index/facts"

DEPLOY_TEXT = "Widget deploys fail when the load balancer caches dead targets"
QUEUE_TEXT = "The widget queue sheds work above five hundred pending jobs"
ROLLBACK_TEXT = "Rolling back a widget release needs the previous image digest pinned"

PROPOSALS = {
    "proposals": [
        {
            "type": "fact", "edit": "new", "target": "legacy-e2e", "topic": "rollbacks",
            "category": "runbook-note", "text": ROLLBACK_TEXT,
            "tags": ["deploy"], "confidence": 0.85, "rationale": "verified this session",
        }
    ]
}

# The v0.2 index: a table the maintainers kept by hand, and drifted. `retired` was folded
# into a skill months ago and the row was never removed — which is exactly why mneme
# regenerates this file rather than editing it.
LEGACY_INDEX = """---
name: knowledge-index
description: Consult when you need durable facts about the widget service — constraints, gotchas, decisions and runbook notes recorded by the team. Topics are listed in this skill and stored in facts/.
---

# legacy-e2e fact index

| Topic | File | Bullets |
|---|---|---|
| deploys | facts/deploys.md | 1 |
| queues | facts/queues.md | 1 |
| retired | facts/retired.md | 3 |
"""

PLUGIN_JSON = {
    "name": "legacy-e2e",
    "description": "Institutional knowledge about the widget service.",
    "version": "0.2.0",
}


def sh(env, *args, cwd=None):
    return subprocess.run(
        list(args), capture_output=True, text=True, env=env,
        cwd=str(cwd) if cwd else None,
    )


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=e2e", "-c", "user.email=e2e@localhost",
         *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def in_tree(repo, ref, path):
    """Does `path` exist in `ref`'s tree? — asked without raising, so a missing routing
    target reads as a failed assertion naming the path rather than a git traceback."""
    return subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{ref}:{path}"],
        capture_output=True, text=True,
    ).returncode == 0


def fact_file(topic, category, text, tag):
    return (
        f"---\ntopic: {topic}\n---\n"
        f"- [{category}] {text} #{tag} (verified: 2026-02-10)\n"
    )


def write_v02_repo(root):
    """A knowledge plugin exactly as mneme v0.2 left it: facts at the repo root."""
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(PLUGIN_JSON, indent=2) + "\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# legacy-e2e\n\nWidget service knowledge.\n", encoding="utf-8")
    index = root / "skills" / "knowledge-index"
    index.mkdir(parents=True)
    (index / "SKILL.md").write_text(LEGACY_INDEX, encoding="utf-8")
    facts = root / "facts"
    facts.mkdir()
    (facts / "deploys.md").write_text(
        fact_file("deploys", "gotcha", DEPLOY_TEXT, "deploy"), encoding="utf-8"
    )
    (facts / "queues.md").write_text(
        fact_file("queues", "constraint", QUEUE_TEXT, "queue"), encoding="utf-8"
    )
    git(root, "init", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-m", "seed the v0.2 knowledge repo")


def index_rows(text):
    rows = [
        [c.strip() for c in line.strip("|").split("|")]
        for line in text.splitlines()
        if line.startswith("|")
    ]
    return [r for r in rows if r[0] not in ("Topic", "---")]


def test_legacy_repo_upgrade(tmp_path):
    home = tmp_path / "home"
    env = dict(os.environ, MNEME_HOME=str(home), CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
    env.pop("MNEME_DISTILLING", None)
    mneme = str(REPO_ROOT / "bin" / "mneme")

    # 1. a v0.2 repo, registered the way an existing knowledge plugin is registered
    kb = tmp_path / "legacy-e2e"
    write_v02_repo(kb)
    r = sh(env, mneme, "registry", "add", "legacy-e2e", "--repo", f"local:{kb}",
           "--path", str(kb))
    assert r.returncode == 0, r.stderr
    assert "legacy facts layout: legacy-e2e" in sh(env, mneme, "status").stdout

    # 2. one new fact, staged through the normal pipeline
    proposals = tmp_path / "proposals.json"
    proposals.write_text(json.dumps(PROPOSALS), encoding="utf-8")
    r = sh(env, mneme, "distill", "ingest", str(proposals), "--source", "e2e")
    assert r.returncode == 0, r.stderr
    ids = [
        line.split()[0]
        for line in sh(env, mneme, "share", "list").stdout.splitlines()
        if line.startswith("  ")
    ]
    assert len(ids) == 1, ids

    # 3. the contribution — and the only thing the user does
    main_before = git(kb, "rev-parse", "main")
    r = sh(env, mneme, "share", "apply", "--ids", ids[0], "--no-push")
    assert r.returncode == 0, r.stderr
    branch = next(
        b.strip("* ").strip()
        for b in git(kb, "branch").splitlines()
        if "mneme/harvest-" in b
    )

    # 4. on the branch: every fact canonical, nothing at the root
    tree = git(kb, "ls-tree", "-r", "--name-only", branch).splitlines()
    assert sorted(p for p in tree if p.startswith(f"{CANON}/")) == [
        f"{CANON}/deploys.md", f"{CANON}/queues.md", f"{CANON}/rollbacks.md"
    ]
    assert [p for p in tree if p.startswith("facts/")] == []
    canonical_deploys = git(kb, "show", f"{branch}:{CANON}/deploys.md")
    assert DEPLOY_TEXT in canonical_deploys
    assert QUEUE_TEXT in git(kb, "show", f"{branch}:{CANON}/queues.md")
    assert ROLLBACK_TEXT in git(kb, "show", f"{branch}:{CANON}/rollbacks.md")

    # 5. the router was regenerated: every topic listed, every File cell resolving from
    #    the skill directory, and the stale row the v0.2 table carried is gone.
    rows = index_rows(git(kb, "show", f"{branch}:skills/knowledge-index/SKILL.md"))
    assert sorted(r[0] for r in rows) == ["deploys", "queues", "rollbacks"]
    for topic, cell, count in rows:
        resolved = f"skills/knowledge-index/{cell}"
        assert in_tree(kb, branch, resolved), resolved
        assert count == "1", (topic, count)

    # 6. the move kept the history: the seeding commit is still reachable from the file
    #    at its new path.
    history = git(kb, "log", "--follow", "--format=%s", branch, "--", f"{CANON}/deploys.md")
    assert "seed the v0.2 knowledge repo" in history.splitlines()

    # 7. the commit says what it did to the layout, so a reviewer reads a move as a move
    body = git(kb, "log", branch, "-1", "--format=%B")
    assert "Migrated:" in body
    assert f"facts/deploys.md -> {CANON}/deploys.md" in body
    assert f"facts/queues.md -> {CANON}/queues.md" in body

    # 8. PR-only: main did not move, and is still exactly the legacy repo it was
    assert git(kb, "rev-parse", "main") == main_before
    assert git(kb, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(kb, "status", "--porcelain") == ""
    assert (kb / "facts" / "deploys.md").is_file()
    assert not (kb / CANON).exists()

    # 9. the human merges the branch — and retrieval spans the seam: what moved is found
    #    at its new path, beside what arrived.
    git(kb, "merge", branch)
    assert not (kb / "facts").exists()
    assert sh(env, mneme, "index", "rebuild").returncode == 0
    for query, expected in (
        ("load balancer caches dead targets", "facts/deploys"),
        ("queue sheds work above five hundred", "facts/queues"),
        ("rollback previous image digest", "facts/rollbacks"),
    ):
        r = sh(env, mneme, "search", query)
        assert r.returncode == 0, r.stderr
        assert expected in r.stdout, (query, r.stdout)
