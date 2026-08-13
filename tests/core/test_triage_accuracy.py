"""Triage accuracy: what a maintainer is told about an inbound pull request must be true.

Four gaps the Plan 11 audit demonstrated, each closed deterministically here: a fact that
`/mneme:classify` already filed into a skill is recognized as integrated without a
database; a decline recorded for one knowledge repo does not silence the same sentence
proposed to another; a PR listing that hit its limit says so instead of reading as "all of
them"; and the bundle states which clone it read, because a "new" label computed against a
stale tree can be wrong.
"""
import json
import os
import stat
from pathlib import Path

from mneme_core import gitops, indexing, paths, review, scaffold, staging, units
from mneme_core.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]


def shim_gh(tmp_path, monkeypatch, pr_list_json, diffs):
    """A `gh` on PATH that answers list/diff from files — and logs every call it got."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    (bindir / "prlist.json").write_text(pr_list_json, encoding="utf-8")
    for n, diff in diffs.items():
        (bindir / f"diff{n}.txt").write_text(diff, encoding="utf-8")
    gh = bindir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{bindir}/calls.txt"\n'
        'case "$*" in\n'
        f'  *"pr list"*) cat "{bindir}/prlist.json" ;;\n'
        f'  *"pr diff"*) n=$(echo "$@" | grep -o "[0-9]*" | head -1); cat "{bindir}/diff$n.txt" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return bindir


def gh_calls(tmp_path):
    return (tmp_path / "fakebin" / "calls.txt").read_text(encoding="utf-8")


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


INTEGRATED = "Sidecar draining requires a preStop hook"
ROUTER_ONLY = "The widget queue caps at 500 jobs"
DECLINED_TEXT = "We standardised on blue-green rollouts"
# The declined bullet as a human rejected it months ago: a decline keys on the sentence,
# so the re-proposal below carries a newer verified date and still matches.
DECLINED = f"- [decision] {DECLINED_TEXT} #rollout (verified: 2026-06-01)"
ROUTER_ONLY_BULLET = f"- [constraint] {ROUTER_ONLY} #limits (verified: 2026-05-01)"

PR_LIST = json.dumps(
    [
        {"number": 7, "title": "sidecar + queue facts", "headRefName": "feature/sidecars",
         "author": {"login": "alice"}, "url": "https://example.com/pr/7"},
        {"number": 9, "title": "cron gotcha", "headRefName": "feature/cron",
         "author": {"login": "bob"}, "url": "https://example.com/pr/9"},
    ]
)

DIFF7 = f"""diff --git a/skills/knowledge-index/facts/sidecars.md b/skills/knowledge-index/facts/sidecars.md
new file mode 100644
--- /dev/null
+++ b/skills/knowledge-index/facts/sidecars.md
@@ -0,0 +1,6 @@
+---
+topic: sidecars
+---
+- [runbook-note] {INTEGRATED} #sidecar (verified: 2026-08-12)
+- [constraint] {ROUTER_ONLY} #limits (verified: 2026-08-12)
+- [decision] {DECLINED_TEXT} #rollout (verified: 2026-08-12)
"""

DIFF9 = """diff --git a/skills/knowledge-index/facts/cron.md b/skills/knowledge-index/facts/cron.md
new file mode 100644
--- /dev/null
+++ b/skills/knowledge-index/facts/cron.md
@@ -0,0 +1,4 @@
+---
+topic: cron
+---
+- [gotcha] The nightly job double-fires on the DST switch #cron (verified: 2026-08-12)
"""

# The integrated sentence is wrapped across two prose lines: an integration is a sentence
# inside a paragraph, not a line of its own, so matching has to survive re-wrapping.
_WRAPPED = INTEGRATED.replace("requires", "requires\n")
SIDECAR_SKILL = f"""---
name: sidecar-drain
description: Use when sidecar draining stalls and a preStop hook is required
---

## Procedure

Drain the sidecar first. {_WRAPPED}, so add one before you roll out.
"""


def make_kb(tmp_path, name="lib-kb"):
    """A registered plugin whose sidecar skill already carries one of the PR's sentences."""
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo")
    skill = target / "skills" / "sidecar-drain"
    skill.mkdir()
    (skill / "SKILL.md").write_text(SIDECAR_SKILL, encoding="utf-8")
    # The generated router names topics, never sentences — but a repo whose router happens
    # to quote one must not read as an integration: it is the facts talking about themselves.
    router = target / "skills" / "knowledge-index" / "SKILL.md"
    router.write_text(
        router.read_text(encoding="utf-8") + f"\nSee also: {ROUTER_ONLY}.\n", encoding="utf-8"
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "fixtures")
    return home, target


def fact_candidate(body, target):
    return staging.Candidate(
        id=staging.candidate_id("fact", target, body),
        type="fact", edit="new", target=target, body=body,
    )


def statuses(pr):
    return {f["text"]: f["status"] for f in pr["facts"]}


def facts_of(bundle, pr_index=0):
    return bundle["prs"][pr_index]["facts"]


# --- 1. "already integrated" is read from the skills themselves, not guessed ------------


def test_a_fact_written_into_a_skill_is_labeled_already_integrated(tmp_path, monkeypatch):
    """No index, no database: the skill prose itself is the evidence."""
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    bundle = review.triage(home, target)

    fact = next(f for f in facts_of(bundle) if f["text"] == INTEGRATED)
    assert fact["integrated"] is True
    assert fact["status"] == "already-integrated"
    assert fact["similar_to"] == ""


def test_a_sentence_only_in_the_generated_router_is_not_an_integration(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    bundle = review.triage(home, target)

    fact = next(f for f in facts_of(bundle) if f["text"] == ROUTER_ONLY)
    assert fact["integrated"] is False
    assert fact["status"] == "new"


def test_already_integrated_outranks_the_index_hint(tmp_path, monkeypatch):
    """`similar_to` raises a question; the sentence in the skill answers it."""
    home, target = make_kb(tmp_path)
    indexing.rebuild(home)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    bundle = review.triage(home, target)

    fact = next(f for f in facts_of(bundle) if f["text"] == INTEGRATED)
    assert fact["similar_to"] == "skills/sidecar-drain"
    assert fact["status"] == "already-integrated"


def test_an_unreadable_skill_file_does_not_hide_the_integrations_beside_it(tmp_path, monkeypatch):
    """Triage parsing is total: one undecodable skill costs its own evidence, nothing else."""
    home, target = make_kb(tmp_path)
    broken = target / "skills" / "broken-skill"
    broken.mkdir()
    (broken / "SKILL.md").write_bytes(b"---\nname: broken\n---\n\xff\xfe not utf-8\n")
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    bundle = review.triage(home, target)

    assert statuses(bundle["prs"][0])[INTEGRATED] == "already-integrated"


# --- 2. declines are scoped to the plugin they were recorded for -----------------------


def test_decline_records_the_plugin_the_candidate_was_staged_for(tmp_path):
    home = tmp_path / "home"

    staging.decline(home, fact_candidate(DECLINED, "lib-kb"), "not durable enough")

    record = json.loads(paths.declined_path(home).read_text(encoding="utf-8").splitlines()[-1])
    assert record["target"] == "lib-kb"


def test_is_declined_answers_per_plugin_and_unscoped_callers_see_every_decline(tmp_path):
    home = tmp_path / "home"
    staging.decline(home, fact_candidate(DECLINED, "lib-kb"), "not durable enough")

    assert staging.is_declined(home, DECLINED, plugin="lib-kb") is True
    assert staging.is_declined(home, DECLINED, plugin="ops-kb") is False
    assert staging.is_declined(home, DECLINED) is True


def test_a_decline_for_one_plugin_does_not_mark_the_same_fact_for_another(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path)
    other = scaffold.create(home, "ops-kb", owner="demo")
    staging.decline(home, fact_candidate(DECLINED, "lib-kb"), "not durable enough")
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    mine = review.triage(home, target)
    theirs = review.triage(home, other)

    assert statuses(mine["prs"][0])[DECLINED_TEXT] == "declined"
    assert statuses(theirs["prs"][0])[DECLINED_TEXT] == "new"


def test_a_legacy_decline_with_no_target_still_applies_everywhere(tmp_path, monkeypatch):
    """Ledger lines predating the field are a verdict on the knowledge, not on one repo."""
    home, target = make_kb(tmp_path)
    other = scaffold.create(home, "ops-kb", owner="demo")
    paths.ensure_layout(home)
    with paths.declined_path(home).open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "id": "fact-legacy",
                    "hash": units.semantic_hash(ROUTER_ONLY_BULLET),
                    "text_hash": units.fact_text_hash(ROUTER_ONLY_BULLET),
                    "reason": "declined before declines carried a target",
                    "ts": "2026-01-01T00:00:00+00:00",
                }
            )
            + "\n"
        )
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    assert statuses(review.triage(home, target)["prs"][0])[ROUTER_ONLY] == "declined"
    assert statuses(review.triage(home, other)["prs"][0])[ROUTER_ONLY] == "declined"


def test_a_decline_with_no_destination_stays_global(tmp_path, monkeypatch):
    """An unassigned candidate was rejected as knowledge, not as one repo's knowledge."""
    home, target = make_kb(tmp_path)
    other = scaffold.create(home, "ops-kb", owner="demo")
    staging.decline(home, fact_candidate(DECLINED, staging.UNASSIGNED), "no home for this")
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    assert statuses(review.triage(home, target)["prs"][0])[DECLINED_TEXT] == "declined"
    assert statuses(review.triage(home, other)["prs"][0])[DECLINED_TEXT] == "declined"


def test_ingest_only_skips_a_decline_recorded_for_that_target(tmp_path, capsys):
    home = tmp_path / "home"
    body = "- [constraint] DB resets nightly #staging (verified: 2026-05-01)"
    staging.decline(home, fact_candidate(body, "acme-knowledge"), "not useful")
    proposals = tmp_path / "proposals.json"
    proposals.write_text(
        json.dumps(
            {
                "proposals": [
                    {"type": "fact", "edit": "new", "target": "other-knowledge",
                     "topic": "staging-env", "category": "constraint",
                     "text": "DB resets nightly", "tags": ["staging"],
                     "confidence": 0.7, "rationale": "observed twice"}
                ]
            }
        ),
        encoding="utf-8",
    )

    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", str(proposals))

    assert code == 0
    assert "staged 1" in out
    assert [c.target for c in staging.load_candidates(home)] == ["other-knowledge"]


# --- 3. a capped PR listing says so ----------------------------------------------------


def test_list_open_prs_reports_whether_the_listing_filled_its_limit(tmp_path, monkeypatch):
    shim_gh(tmp_path, monkeypatch, PR_LIST, {})

    prs, truncated = gitops.list_open_prs(tmp_path, limit=2)
    assert [p["number"] for p in prs] == [7, 9]
    assert truncated is True

    _prs, truncated = gitops.list_open_prs(tmp_path, limit=3)
    assert truncated is False


def test_list_open_prs_asks_for_a_hundred_by_default(tmp_path, monkeypatch):
    shim_gh(tmp_path, monkeypatch, PR_LIST, {})

    gitops.list_open_prs(tmp_path)

    assert "--limit 100" in gh_calls(tmp_path)


def test_a_truncated_listing_is_carried_into_the_bundle_with_a_note(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})
    real = gitops.list_open_prs
    monkeypatch.setattr(gitops, "list_open_prs", lambda repo, limit=2: real(repo, limit=limit))

    bundle = review.triage(home, target)

    assert bundle["truncated"] is True
    assert "2" in bundle["note"] and "more" in bundle["note"].lower()
    assert [p["number"] for p in bundle["prs"]] == [7, 9]


def test_a_listing_that_fits_is_not_reported_as_truncated(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    bundle = review.triage(home, target)

    assert bundle["truncated"] is False
    assert "note" not in bundle


# --- 4. the bundle states which clone it was computed against ---------------------------


def test_head_names_the_branch_and_sha_and_reports_no_remote_to_compare(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    head = review.triage(home, target)["head"]

    assert head["branch"] == "main"
    assert head["sha"] == gitops.head_sha(target)
    assert head["behind_remote"] is None


def test_head_is_not_behind_when_the_remote_ref_matches(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path)
    gitops.git(target, "update-ref", "refs/remotes/origin/main", gitops.head_sha(target))
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    assert review.triage(home, target)["head"]["behind_remote"] is False


def test_head_is_behind_when_origin_main_carries_a_commit_this_clone_lacks(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path)
    (target / "README.md").write_text("# later\n", encoding="utf-8")
    gitops.git(target, "commit", "-am", "a commit only the remote has")
    gitops.git(target, "update-ref", "refs/remotes/origin/main", gitops.head_sha(target))
    gitops.git(target, "reset", "--hard", "HEAD~1")
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    bundle = review.triage(home, target)

    assert bundle["head"]["behind_remote"] is True
    assert bundle["head"]["sha"] == gitops.head_sha(target)


def test_the_instructions_tell_the_agent_to_report_a_stale_clone_and_a_capped_listing(tmp_path, monkeypatch, capsys):
    home, target = make_kb(tmp_path)
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: DIFF7, 9: DIFF9})

    code, out, _ = run(capsys, "--home", str(home), "review", "triage", "--cwd", str(target))

    assert code == 0
    instructions = json.loads(out)["instructions"]
    assert "behind_remote" in instructions
    assert "truncated" in instructions
    assert "already-integrated" in instructions


def test_the_shipped_review_skill_names_every_label_and_caveat_triage_emits():
    """The skill is what the agent reads first: a label or caveat missing there is invisible."""
    body = (REPO_ROOT / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")

    for label in ("duplicate", "declined", "already-integrated", "possibly-integrated", "new"):
        assert label in body
    assert "behind_remote" in body
    assert "truncated" in body
