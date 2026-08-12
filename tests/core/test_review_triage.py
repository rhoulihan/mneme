import json
import os
import stat

from mneme_core import gitops, indexing, review, scaffold, staging, units
from mneme_core.cli import main


def shim_gh(tmp_path, monkeypatch, pr_list_json, diffs):
    """diffs: dict number->unified diff text, written to files the shim cats."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    (bindir / "prlist.json").write_text(pr_list_json, encoding="utf-8")
    for n, diff in diffs.items():
        (bindir / f"diff{n}.txt").write_text(diff, encoding="utf-8")
    gh = bindir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *"pr list"*) cat "{bindir}/prlist.json" ;;\n'
        f'  *"pr diff"*) n=$(echo "$@" | grep -o "[0-9]*" | head -1); cat "{bindir}/diff$n.txt" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# The repo already carries this one; the declined body was rejected by a human months ago
# (on an older verified date — a decline must hold regardless of when it is re-proposed).
EXISTING = "- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-11)"
DECLINED = "- [decision] We standardised on blue-green rollouts #rollout (verified: 2026-06-01)"

PR_LIST = json.dumps(
    [
        {"number": 7, "title": "add deploy facts", "headRefName": "feature/deploys",
         "author": {"login": "alice"}, "url": "https://example.com/pr/7"},
        {"number": 9, "title": "sidecar runbook", "headRefName": "feature/sidecars",
         "author": {"login": "bob"}, "url": "https://example.com/pr/9"},
    ]
)

DIFF7 = """diff --git a/skills/knowledge-index/facts/deploys.md b/skills/knowledge-index/facts/deploys.md
--- a/skills/knowledge-index/facts/deploys.md
+++ b/skills/knowledge-index/facts/deploys.md
@@ -3,1 +3,5 @@
 - [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-11)
+- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-12)
+- [constraint] The widget queue caps at 500 jobs #limits (verified: 2026-08-12)
+- [decision] We standardised on blue-green rollouts #rollout (verified: 2026-08-12)
+- [broken bullet with no close
diff --git a/skills/new-skill/SKILL.md b/skills/new-skill/SKILL.md
new file mode 100644
--- /dev/null
+++ b/skills/new-skill/SKILL.md
@@ -0,0 +1,2 @@
+---
+name: new-skill
"""

DIFF9 = """diff --git a/skills/knowledge-index/facts/sidecars.md b/skills/knowledge-index/facts/sidecars.md
new file mode 100644
--- /dev/null
+++ b/skills/knowledge-index/facts/sidecars.md
@@ -0,0 +1,4 @@
+---
+topic: sidecars
+---
+- [runbook-note] Sidecar draining requires a preStop hook #sidecar (verified: 2026-08-12)
+- [constraint] The widget queue caps at 500 jobs #limits (verified: 2026-08-12)
"""


def make_kb(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "lib-kb", owner="demo")
    skill = target / "skills" / "sidecar-drain"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: sidecar-drain\n"
        "description: Use when sidecar draining stalls and a preStop hook is required\n"
        "---\n\n## Procedure\n\nDrain, then restart.\n",
        encoding="utf-8",
    )
    facts = target / units.FACTS_CANONICAL
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "deploys.md").write_text(
        f"---\ntopic: deploys\n---\n{EXISTING}\n", encoding="utf-8"
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "fixtures")
    staging.decline(
        home,
        staging.Candidate(
            id=staging.candidate_id("fact", "lib-kb", DECLINED),
            type="fact", edit="new", target="lib-kb", body=DECLINED,
        ),
        "not durable enough",
    )
    return home, target


def statuses(pr):
    return {f["text"]: f["status"] for f in pr["facts"]}


def test_triage_labels_duplicate_declined_and_new(tmp_path, monkeypatch):
    """Each label is evidence the maintainer can act on — and none of it needs the index."""
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    bundle = review.triage(home, target)

    assert bundle["plugin"] == "lib-kb"
    assert bundle["repo"] == str(target)
    pr7 = bundle["prs"][0]
    assert (pr7["number"], pr7["author"], pr7["url"]) == (7, "alice", "https://example.com/pr/7")
    by_text = statuses(pr7)
    assert by_text["Deploys fail when the LB caches dead targets"] == "duplicate"
    assert by_text["The widget queue caps at 500 jobs"] == "new"
    assert by_text["We standardised on blue-green rollouts"] == "declined"
    dup = next(f for f in pr7["facts"] if f["status"] == "duplicate")
    assert dup["duplicate"] is True and dup["declined"] is False
    assert dup["unit_id"] == "facts/deploys#deploys-fail-when-the-lb-caches"
    # No index database in this home: the hint is absent, never a blocker.
    assert all(f["similar_to"] == "" for pr in bundle["prs"] for f in pr["facts"])


def test_cross_pr_duplicate_is_flagged_against_the_earlier_pr(tmp_path, monkeypatch):
    """Two PRs proposing the same fact: the first is new, the second is a duplicate."""
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    bundle = review.triage(home, target)

    assert statuses(bundle["prs"][0])["The widget queue caps at 500 jobs"] == "new"
    assert statuses(bundle["prs"][1])["The widget queue caps at 500 jobs"] == "duplicate"
    assert statuses(bundle["prs"][1])["Sidecar draining requires a preStop hook"] == "new"


def test_skipped_additions_and_new_skills_are_surfaced(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    bundle = review.triage(home, target)

    pr7 = bundle["prs"][0]
    assert len(pr7["skipped"]) == 1 and "broken" in pr7["skipped"][0]
    assert pr7["skills_added"] == [
        {"pr": 7, "file": "skills/new-skill/SKILL.md", "name": "new-skill"}
    ]
    assert bundle["prs"][1]["skills_added"] == []


def test_possibly_integrated_when_the_index_points_at_a_skill(tmp_path, monkeypatch):
    """The index's nearest unit is a hint: a skill hit means 'maybe already covered'."""
    home, target = make_kb(tmp_path)
    indexing.rebuild(home)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    bundle = review.triage(home, target)

    fact = next(
        f for f in bundle["prs"][1]["facts"]
        if f["text"] == "Sidecar draining requires a preStop hook"
    )
    assert fact["similar_to"] == "skills/sidecar-drain"
    assert fact["status"] == "possibly-integrated"
    # Evidence, not a verdict: an exact duplicate stays a duplicate whatever the index says.
    assert statuses(bundle["prs"][0])["Deploys fail when the LB caches dead targets"] == "duplicate"


def test_cli_triage_prints_the_bundle_as_json(tmp_path, monkeypatch, capsys):
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    code, out, _ = run(capsys, "--home", str(home), "review", "triage", "--cwd", str(target / "skills"))

    assert code == 0
    bundle = json.loads(out)
    assert [p["number"] for p in bundle["prs"]] == [7, 9]
    assert "explicit approval" in bundle["instructions"]
    assert "mneme review begin" in bundle["instructions"]
    assert "mneme review finalize" in bundle["instructions"]


def test_no_open_prs_is_a_valid_empty_bundle(tmp_path, monkeypatch, capsys):
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, "[]", {})

    code, out, _ = run(capsys, "--home", str(home), "review", "triage", "--cwd", str(target))

    assert code == 0
    assert json.loads(out)["prs"] == []


def test_missing_gh_fails_with_exit_one(tmp_path, monkeypatch, capsys):
    home, target = make_kb(tmp_path)
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    code, _out, err = run(capsys, "--home", str(home), "review", "triage", "--cwd", str(target))

    assert code == 1
    assert "gh" in err
