"""The maintainer loop end to end, through the real entry points (spec §7.8).

This test is the scripted stand-in for the in-session agent: `/mneme:review` reads the
triage bundle, proposes a verdict per pull request, and — once the user approves — writes
ONLY the approved bullets into the facts directory. Here those writes are made by the test
instead of by a model, so everything around them (the gh-backed triage, the annotation, the
review rails, the finalize gates, the PR-only delivery) is exercised for real.

The `gh` on PATH is a shim that also LOGS every invocation, which is what lets this test
assert the constraint no unit test can: the rails never merge, close, or comment on a
contributor's pull request. Those are agent actions, gated on the user's explicit approval,
and nothing under `mneme review …` may reach for them.
"""
import json
import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Already committed in the repo — PR 4 re-proposes it with today's date (the ordinary
# duplicate), alongside one genuinely new fact and one bullet nothing can parse.
EXISTING = "- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-11)"
NEW_FACT = (
    "- [runbook-note] Sidecar draining requires a preStop hook #sidecar (verified: 2026-08-12)"
)

PR_LIST = json.dumps(
    [
        {
            "number": 4,
            "title": "deploy + sidecar knowledge",
            "headRefName": "feature/sidecars",
            "author": {"login": "alice"},
            "url": "https://example.com/pr/4",
        }
    ]
)

DIFF4 = f"""diff --git a/skills/knowledge-index/facts/deploys.md b/skills/knowledge-index/facts/deploys.md
--- a/skills/knowledge-index/facts/deploys.md
+++ b/skills/knowledge-index/facts/deploys.md
@@ -3,1 +3,3 @@
 {EXISTING}
+- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-12)
+- [broken bullet that never closes
diff --git a/skills/knowledge-index/facts/sidecars.md b/skills/knowledge-index/facts/sidecars.md
new file mode 100644
--- /dev/null
+++ b/skills/knowledge-index/facts/sidecars.md
@@ -0,0 +1,4 @@
+---
+topic: sidecars
+---
+{NEW_FACT}
"""


def sh(env, *args, cwd=None):
    return subprocess.run(
        list(args), capture_output=True, text=True, env=env,
        cwd=str(cwd) if cwd else None,
    )


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def shim_gh(bindir, pr_list_json, diffs):
    """A fake `gh` serving canned PR data, and logging every call it is asked to make."""
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / "prlist.json").write_text(pr_list_json, encoding="utf-8")
    for n, diff in diffs.items():
        (bindir / f"diff{n}.txt").write_text(diff, encoding="utf-8")
    log = bindir / "gh.log"
    gh = bindir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{log}"\n'
        'case "$*" in\n'
        f'  *"pr list"*) cat "{bindir}/prlist.json" ;;\n'
        f'  *"pr diff"*) n=$(echo "$@" | grep -o "[0-9]*" | head -1); cat "{bindir}/diff$n.txt" ;;\n'
        '  *"pr create"*) echo "https://example.com/pr/99" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    return log


def test_review_loop(tmp_path):
    home = tmp_path / "home"
    bindir = tmp_path / "fakebin"
    gh_log = shim_gh(bindir, PR_LIST, {4: DIFF4})
    env = dict(
        os.environ,
        MNEME_HOME=str(home),
        CLAUDE_PLUGIN_ROOT=str(REPO_ROOT),
        PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
    )
    env.pop("MNEME_DISTILLING", None)
    mneme = str(REPO_ROOT / "bin" / "mneme")

    # 1. scaffold + register, seed the fact the repo already carries, publish main
    r = sh(env, mneme, "new", "review-e2e", "--owner", "e2e-team")
    assert r.returncode == 0, r.stderr
    kb = Path(
        json.loads((home / "registry.json").read_text(encoding="utf-8"))["plugins"][0]["path"]
    )
    facts_dir = kb / "skills" / "knowledge-index" / "facts"
    facts_dir.mkdir(parents=True, exist_ok=True)
    (facts_dir / "deploys.md").write_text(
        f"---\ntopic: deploys\n---\n{EXISTING}\n", encoding="utf-8"
    )
    git(kb, "add", "-A")
    git(kb, "commit", "-m", "seed deploy facts")
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    git(kb, "remote", "add", "origin", str(remote))
    git(kb, "push", "-u", "origin", "main")

    # 2. triage: every open PR, every addition annotated with evidence
    r = sh(env, mneme, "review", "triage", "--cwd", str(kb / "skills"))
    assert r.returncode == 0, r.stderr
    bundle = json.loads(r.stdout)
    assert bundle["plugin"] == "review-e2e"
    assert [p["number"] for p in bundle["prs"]] == [4]
    pr = bundle["prs"][0]
    assert (pr["author"], pr["url"]) == ("alice", "https://example.com/pr/4")
    status = {f["text"]: f["status"] for f in pr["facts"]}
    assert status["Deploys fail when the LB caches dead targets"] == "duplicate"
    assert status["Sidecar draining requires a preStop hook"] == "new"
    # Unparseable additions are reported, never fatal — the rest of the PR still triaged.
    assert len(pr["skipped"]) == 1 and "broken" in pr["skipped"][0]
    assert "explicit approval" in bundle["instructions"]

    # 3. the rails: begin on the current directory, no plugin name anywhere
    r = sh(env, mneme, "review", "begin", "--cwd", str(kb / "skills"))
    assert r.returncode == 0, r.stderr
    branch = r.stdout.strip()
    assert branch.startswith("mneme/review-")
    assert git(kb, "rev-parse", "--abbrev-ref", "HEAD") == branch

    # 4. the scripted agent: the user approved the new fact only, so only that bullet is
    #    written — verbatim, keeping its tags and verified date.
    approved = next(f for f in pr["facts"] if f["status"] == "new")
    (facts_dir / f"{approved['stem']}.md").write_text(
        f"---\ntopic: {approved['stem']}\n---\n{approved['line']}\n", encoding="utf-8"
    )

    # 5. finalize: gates, commit, push, PR — and back to an untouched main
    main_before = git(kb, "rev-parse", "main")
    r = sh(env, mneme, "review", "finalize", "--cwd", str(kb))
    assert r.returncode == 0, r.stderr
    assert f"on {branch}" in r.stdout
    assert "pr: https://example.com/pr/99" in r.stdout

    # 6. PR-only delivery: the branch reached the remote, main moved on neither side
    assert branch in git(remote, "branch")
    assert git(kb, "rev-parse", "main") == main_before
    assert git(remote, "rev-parse", "main") == main_before
    assert git(kb, "rev-parse", f"origin/{branch}") == git(kb, "rev-parse", branch)
    assert git(kb, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(kb, "status", "--porcelain") == ""
    assert git(kb, "log", branch, "-1", "--format=%s").startswith("knowledge: review")

    # 7. on the branch (and on the remote): the new fact landed, the duplicate did not,
    #    and the knowledge-index was regenerated to route to the new topic.
    assert NEW_FACT in git(remote, "show", f"{branch}:skills/knowledge-index/facts/sidecars.md")
    deploys = git(kb, "show", f"{branch}:skills/knowledge-index/facts/deploys.md")
    assert deploys.count("- [gotcha]") == 1
    assert "verified: 2026-08-12" not in deploys
    branch_index = git(kb, "show", f"{branch}:skills/knowledge-index/SKILL.md")
    assert "| sidecars |" in branch_index
    assert "| deploys |" in branch_index

    # 8. the ledger records the pass as a review, not a classify
    record = json.loads(
        (home / "submitted.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert record["kind"] == "review"
    assert record["target"] == "review-e2e"
    assert record["branch"] == branch

    # 9. the rails never mutate the contributor's pull request: triage read it, delivery
    #    opened mneme's OWN pr, and merge/close/comment stayed human-approved agent actions.
    calls = gh_log.read_text(encoding="utf-8").splitlines()
    assert any(c.startswith("pr list") for c in calls)
    assert any(c.startswith("pr diff") for c in calls)
    assert not any("pr merge" in c or "pr close" in c or "--comment" in c for c in calls)

    # 10. once the PR is accepted, retrieval finds the extracted knowledge
    git(kb, "merge", branch)
    assert sh(env, mneme, "index", "rebuild").returncode == 0
    r = sh(env, mneme, "search", "sidecar draining preStop hook")
    assert r.returncode == 0, r.stderr
    assert "facts/sidecars" in r.stdout
