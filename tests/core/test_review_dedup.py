"""Duplicate and declined detection key on what a bullet SAYS (spec §7.8).

Both labels used to hash the rendered line, which only ignores the `verified:` stamp — so
the `[category]` prefix and the `#tags`, every character of them contributor-controlled,
were part of the identity. Retagging a known fact made it `new`; recategorizing a fact a
human had DECLINED made it `new` too, which is the "declined stays declined" guarantee
failing to a one-character edit. Identity is the sentence now (`units.fact_text_hash`),
plus the unit id, because two bullets cannot share one id in one topic file.
"""
import json
import os
import stat

from mneme_core import gitops, review, scaffold, staging, units

EXISTING = "- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-11)"
DECLINED = "- [decision] We standardised on blue-green rollouts #rollout (verified: 2026-06-01)"

RETAGGED = "- [gotcha] Deploys fail when the LB caches dead targets #ops (verified: 2026-08-12)"
RECATEGORIZED = (
    "- [constraint] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-12)"
)
BARE = "- [gotcha] Deploys fail when the LB caches dead targets"
DECLINED_AGAIN = "- [decision] We standardised on blue-green rollouts #ops (verified: 2026-08-12)"
GENUINELY_NEW = "- [gotcha] Sidecars stall when the preStop hook is missing #k8s (verified: 2026-08-12)"

PR_LIST = json.dumps(
    [{"number": 5, "title": "deploy knowledge", "author": {"login": "alice"},
      "url": "https://example.com/pr/5"}]
)

DIFF = f"""diff --git a/skills/knowledge-index/facts/deploys.md b/skills/knowledge-index/facts/deploys.md
--- a/skills/knowledge-index/facts/deploys.md
+++ b/skills/knowledge-index/facts/deploys.md
@@ -3,1 +3,6 @@
 {EXISTING}
+{RETAGGED}
+{RECATEGORIZED}
+{BARE}
+{DECLINED_AGAIN}
+{GENUINELY_NEW}
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


def make_kb(tmp_path, name="dedup-kb"):
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo")
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
            id=staging.candidate_id("fact", name, DECLINED),
            type="fact", edit="new", target=name, body=DECLINED,
        ),
        "not durable enough",
    )
    return home, target


def by_line(pr):
    return {f["line"]: f["status"] for f in pr["facts"]}


def test_retagging_or_recategorizing_a_known_fact_is_still_a_duplicate(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {5: DIFF})

    statuses = by_line(review.triage(home, target)["prs"][0])

    assert statuses[RETAGGED] == "duplicate"
    assert statuses[RECATEGORIZED] == "duplicate"
    assert statuses[BARE] == "duplicate"
    assert statuses[GENUINELY_NEW] == "new"


def test_a_declined_fact_stays_declined_under_a_new_tag(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {5: DIFF})

    statuses = by_line(review.triage(home, target)["prs"][0])

    assert statuses[DECLINED_AGAIN] == "declined"


def test_is_declined_holds_across_tag_and_category_edits(tmp_path):
    """The ledger key itself, independent of triage."""
    home = tmp_path / "home"
    cand = staging.Candidate(
        id=staging.candidate_id("fact", "kb", DECLINED),
        type="fact", edit="new", target="kb", body=DECLINED,
    )
    staging.decline(home, cand, "not durable enough")

    assert staging.is_declined(home, DECLINED_AGAIN)
    assert staging.is_declined(
        home, "- [gotcha] We standardised on blue-green rollouts (verified: 2027-01-01)"
    )
    assert not staging.is_declined(
        home, "- [decision] We standardised on canary rollouts #ops (verified: 2026-08-12)"
    )


def test_a_ledger_entry_written_before_the_text_key_still_matches(tmp_path):
    """Old declines carry only `hash`; the rendered line must still be recognized."""
    home = tmp_path / "home"
    from mneme_core import paths

    paths.ensure_layout(home)
    with paths.declined_path(home).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"id": "fact-old", "hash": units.semantic_hash(DECLINED),
                            "reason": "legacy", "ts": "2026-06-01T00:00:00+00:00"}) + "\n")

    assert staging.is_declined(home, DECLINED)


def test_a_colliding_unit_id_is_evidence_too(tmp_path, monkeypatch):
    """Two bullets cannot share one unit id: the topic key IS the identity in a fact file."""
    collides = (
        "- [gotcha] Deploys fail when the LB caches stale DNS records #dns (verified: 2026-08-12)"
    )
    home, target = make_kb(tmp_path, "collide-kb")
    diff = (
        "diff --git a/skills/knowledge-index/facts/deploys.md"
        " b/skills/knowledge-index/facts/deploys.md\n"
        "--- a/skills/knowledge-index/facts/deploys.md\n"
        "+++ b/skills/knowledge-index/facts/deploys.md\n"
        "@@ -3,1 +3,2 @@\n"
        f" {EXISTING}\n"
        f"+{collides}\n"
    )
    shim_gh(tmp_path, monkeypatch, PR_LIST, {5: diff})

    entry = review.triage(home, target)["prs"][0]["facts"][0]

    assert entry["unit_id"] == "facts/deploys#deploys-fail-when-the-lb-caches"
    assert entry["status"] == "duplicate"
