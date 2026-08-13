"""A pull request that DELETES knowledge has to reach the maintainer (spec §7.8).

Triage parsed additions only, so a PR whose diff removes forty fact bullets produced an
empty annotation set — and an agent following the review instructions would read "nothing
to flag" and recommend `merge`, which the user then executes with `gh pr merge`. The same
release made this loss impossible for mneme's own passes (`classify._preservation_gate`:
facts may move, but never vanish), leaving inbound PRs as the one path where knowledge
could still vanish silently. Removals are now their own list, with `moved` separating a
reorganization from a real deletion.
"""
import json
import os
import stat

from mneme_core import gitops, review, scaffold, templates, units

KEPT = "- [constraint] The widget queue caps at 500 jobs #limits (verified: 2026-08-11)"
DOOMED = "- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-11)"

PR_LIST = json.dumps(
    [{"number": 3, "title": "tidy up the facts", "author": {"login": "mallory"},
      "url": "https://example.com/pr/3"}]
)

DELETION_DIFF = f"""diff --git a/skills/knowledge-index/facts/deploys.md b/skills/knowledge-index/facts/deploys.md
--- a/skills/knowledge-index/facts/deploys.md
+++ b/skills/knowledge-index/facts/deploys.md
@@ -3,2 +3,1 @@
-{DOOMED}
 {KEPT}
"""

MOVE_DIFF = f"""diff --git a/skills/knowledge-index/facts/deploys.md b/skills/knowledge-index/facts/deploys.md
--- a/skills/knowledge-index/facts/deploys.md
+++ b/skills/knowledge-index/facts/deploys.md
@@ -3,2 +3,1 @@
-{DOOMED}
 {KEPT}
diff --git a/skills/knowledge-index/facts/platform.md b/skills/knowledge-index/facts/platform.md
new file mode 100644
--- /dev/null
+++ b/skills/knowledge-index/facts/platform.md
@@ -0,0 +1,4 @@
+---
+topic: platform
+---
+{DOOMED}
"""


def shim_gh(tmp_path, monkeypatch, pr_list_json, diffs):
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


def make_kb(tmp_path, name="removal-kb"):
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo")
    facts = target / units.FACTS_CANONICAL
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "deploys.md").write_text(
        f"---\ntopic: deploys\n---\n{DOOMED}\n{KEPT}\n", encoding="utf-8"
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "fixtures")
    return home, target


def test_parse_removed_facts_reads_the_other_side_of_the_hunk():
    removed = review.parse_removed_facts(3, DELETION_DIFF)

    assert [(f.file, f.text) for f in removed] == [
        (
            "skills/knowledge-index/facts/deploys.md",
            "Deploys fail when the LB caches dead targets",
        )
    ]
    assert removed[0].unit_id == "facts/deploys#deploys-fail-when-the-lb-caches"
    # Additions and removals stay separate: a deletion is not a proposal to add.
    assert review.parse_added_facts(3, DELETION_DIFF) == ([], [])


def test_a_deletion_only_pr_is_not_an_empty_bundle(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {3: DELETION_DIFF})

    pr = review.triage(home, target)["prs"][0]

    assert pr["facts"] == []
    assert [r["text"] for r in pr["removed"]] == [
        "Deploys fail when the LB caches dead targets"
    ]
    assert pr["removed"][0]["moved"] is False


def test_a_bullet_moved_between_fact_files_is_marked_moved(tmp_path, monkeypatch):
    """Reorganizing is not forgetting — the label has to tell them apart."""
    home, target = make_kb(tmp_path, "move-kb")
    shim_gh(tmp_path, monkeypatch, PR_LIST, {3: MOVE_DIFF})

    pr = review.triage(home, target)["prs"][0]

    assert [r["moved"] for r in pr["removed"]] == [True]
    # The re-added copy is the fact the repo already carries, so it reads as a duplicate.
    assert [f["status"] for f in pr["facts"]] == ["duplicate"]


def test_the_instructions_tell_the_maintainer_what_a_removal_means():
    text = templates.REVIEW_INSTRUCTIONS
    assert "removed" in text
    assert "delete" in text or "deletes" in text
