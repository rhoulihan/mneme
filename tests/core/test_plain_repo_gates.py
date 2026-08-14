"""Every gate runs in plain mode too. Adding a mode may not create a rail without one.

The rails are shared, so this ought to follow — but "ought to follow" is exactly how a
second code path acquires a hole. The point of these tests is that the preservation gate,
the secret scan, and the lint gate are asserted to fire in a repo whose knowledge lives in
`mneme-index/`, not assumed to because they fire somewhere else.
"""
import pytest

from mneme_core import classify, gitops, harvest, scaffold, staging, units
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id

from tests.core.test_plain_repo_harvest import plain_repo, stage_fact

FACT = "The chargeback webhook replays events for up to seventy two hours"


def seeded_app(tmp_path):
    """An adopted plain repo with one fact already committed on `main`."""
    home, repo = plain_repo(tmp_path)
    scaffold.adopt(home, "payments-service")
    facts = repo / units.FACTS_PLAIN
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "chargebacks.md").write_text(
        "---\ntopic: chargebacks\n---\n"
        f"- [gotcha] {FACT} #chargebacks (verified: 2026-08-14)\n",
        encoding="utf-8",
    )
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "chore: adopt mneme and seed a fact")
    return home, repo


def test_review_extracts_a_fact_into_the_plain_knowledge_root(tmp_path):
    """The rail Rick asked to keep working: facts from an inbound PR, re-authored here."""
    home, repo = seeded_app(tmp_path)
    main_before = gitops.git(repo, "rev-parse", "main")

    branch = classify.review_begin(home, repo)
    new = repo / units.FACTS_PLAIN / "settlement.md"
    new.write_text(
        "---\ntopic: settlement\n---\n"
        "- [constraint] Settlement retries stop after the fifth gateway 429"
        " #settlement (verified: 2026-08-14)\n",
        encoding="utf-8",
    )
    result = classify.review_finalize(home, repo, push=False)

    assert result.branch == branch
    assert gitops.git(repo, "rev-parse", "main") == main_before  # PR-only holds here too
    tree = gitops.git(repo, "ls-tree", "-r", "--name-only", branch).splitlines()
    assert f"{units.FACTS_PLAIN}/settlement.md" in tree
    # The router was regenerated for the plain root, so the new topic is routable.
    router = gitops.git(repo, "show", f"{branch}:mneme-index/SKILL.md")
    assert "| settlement | facts/settlement.md | 1 |" in router
    assert "| chargebacks | facts/chargebacks.md | 1 |" in router


def test_the_preservation_gate_fires_in_a_plain_repo(tmp_path):
    """A fact on `main` that survives nowhere on the branch fails the finalize."""
    home, repo = seeded_app(tmp_path)

    classify.review_begin(home, repo)
    (repo / units.FACTS_PLAIN / "chargebacks.md").write_text(
        "---\ntopic: chargebacks\n---\n", encoding="utf-8"
    )
    with pytest.raises(MnemeError) as e:
        classify.review_finalize(home, repo, push=False)
    assert "chargebacks" in str(e.value)
    assert FACT.split()[0].lower() in str(e.value).lower() or "facts/chargebacks" in str(e.value)


def test_the_secret_scan_fires_in_a_plain_repo(tmp_path):
    """A blocker on the branch stops the pass, in whichever root the file lives."""
    home, repo = seeded_app(tmp_path)

    classify.review_begin(home, repo)
    (repo / units.FACTS_PLAIN / "creds.md").write_text(
        "---\ntopic: creds\n---\n"
        "- [reference] The key is AKIAIOSFODNN7EXAMPLE #aws (verified: 2026-08-14)\n",
        encoding="utf-8",
    )
    with pytest.raises(MnemeError, match="scan"):
        classify.review_finalize(home, repo, push=False)


def test_the_lint_gate_fires_in_a_plain_repo(tmp_path):
    """A malformed bullet in `mneme-index/facts/` is linted, exactly as elsewhere."""
    home, repo = seeded_app(tmp_path)

    classify.review_begin(home, repo)
    (repo / units.FACTS_PLAIN / "broken.md").write_text(
        "---\ntopic: broken\n---\n- [not-a-category] something #x (verified: 2026-08-14)\n",
        encoding="utf-8",
    )
    with pytest.raises(MnemeError, match="lint"):
        classify.review_finalize(home, repo, push=False)


def test_a_quarantined_candidate_never_reaches_a_plain_repo(tmp_path):
    home, repo = plain_repo(tmp_path)
    body = "- [reference] token AKIAIOSFODNN7EXAMPLE #aws (verified: 2026-08-14)\n"
    cand = Candidate(
        id=candidate_id("fact", "payments-service", body), type="fact", edit="new",
        target="payments-service", body=body, topic="creds", status="quarantined",
    )
    staging.write_candidate(home, cand)
    with pytest.raises(MnemeError, match="quarantined"):
        harvest.apply_batch(home, "payments-service", [cand], push=False)


def test_a_failed_gate_leaves_the_plain_repo_exactly_as_it_was(tmp_path):
    """The rollback is the rails', so it must reach `mneme-index/` too."""
    home, repo = seeded_app(tmp_path)
    before = gitops.git(repo, "rev-parse", "main")

    classify.review_begin(home, repo)
    (repo / units.FACTS_PLAIN / "creds.md").write_text(
        "---\ntopic: creds\n---\n"
        "- [reference] The key is AKIAIOSFODNN7EXAMPLE #aws (verified: 2026-08-14)\n",
        encoding="utf-8",
    )
    with pytest.raises(MnemeError):
        classify.review_finalize(home, repo, push=False)

    assert gitops.git(repo, "rev-parse", "main") == before
    assert gitops.current_branch(repo) == "main"
    assert gitops.is_clean(repo)
    assert not (repo / units.FACTS_PLAIN / "creds.md").exists()


# --- the layout predicates themselves ---------------------------------------
#
# `units.is_fact_path` and `units.in_knowledge_root` replaced four literals that each
# encoded "facts live in exactly two places" and "the router is at
# `skills/knowledge-index/`". Their contracts are tested here rather than only through the
# gates that call them: a mutation run showed the traversal defence and the plain-router
# exclusion both surviving, because every caller happened to reach them by a path where the
# property did not yet bite. A rule stated once needs its own test once.


@pytest.mark.parametrize(
    "rel",
    [
        "skills/knowledge-index/facts/deploys.md",
        "mneme-index/facts/deploys.md",
        "facts/deploys.md",
    ],
)
def test_every_layout_is_a_fact_path(rel):
    assert units.is_fact_path(rel)


@pytest.mark.parametrize(
    "rel",
    [
        "facts/../../etc/passwd.md",          # traversal, spelled as a path
        "vendor/facts/deploys.md",            # a nested lookalike
        "mneme-index/facts/archive/old.md",   # a SUBdirectory no reader sweeps
        "skills/knowledge-index/facts/sub/x.md",
        "mneme-index/SKILL.md",               # the router, not a fact file
        "mneme-index/facts/deploys.txt",      # not markdown
        "src/factsheet.md",                   # a "facts" substring, not a facts directory
    ],
)
def test_a_lookalike_is_not_a_fact_path(rel):
    """Whole-path matching. A sniffed "facts" segment makes every one of these a fact."""
    assert not units.is_fact_path(rel)


@pytest.mark.parametrize(
    "rel",
    ["skills/knowledge-index/SKILL.md", "skills/knowledge-index/facts/x.md",
     "mneme-index/SKILL.md", "mneme-index/facts/x.md"],
)
def test_both_generated_roots_are_knowledge_roots(rel):
    """The router is regenerated FROM the facts, in either mode — never evidence of one."""
    assert units.in_knowledge_root(rel)


@pytest.mark.parametrize("rel", ["skills/deploy-widget/SKILL.md", "README.md", "src/app.py"])
def test_an_ordinary_path_is_not_a_knowledge_root(rel):
    assert not units.in_knowledge_root(rel)


def test_review_parses_a_fact_added_in_the_plain_layout(tmp_path):
    """Triage reads inbound diffs. A layout it cannot match is knowledge it cannot see."""
    from mneme_core import review

    assert review._fact_stem("mneme-index/facts/chargebacks.md") == "chargebacks"
    assert review._fact_stem("skills/knowledge-index/facts/chargebacks.md") == "chargebacks"
    assert review._fact_stem("facts/chargebacks.md") == "chargebacks"
    assert review._fact_stem("mneme-index/SKILL.md") is None
    assert review._fact_stem("vendor/facts/x.md") is None


def test_an_app_s_own_skill_can_never_account_for_a_deleted_fact(tmp_path):
    """In a plain repo NOTHING carries knowledge — `skills/` there is the application's."""
    home, repo = seeded_app(tmp_path)
    (repo / "skills" / "combat").mkdir(parents=True)
    (repo / "skills" / "combat" / "SKILL.md").write_text(
        f"---\nname: combat\ndescription: the app's own\n---\n\n{FACT}\n", encoding="utf-8"
    )
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "the app's own skill quotes the same sentence")

    classify.review_begin(home, repo)
    (repo / units.FACTS_PLAIN / "chargebacks.md").write_text(
        "---\ntopic: chargebacks\n---\n", encoding="utf-8"
    )
    (repo / "skills" / "combat" / "SKILL.md").write_text(
        f"---\nname: combat\ndescription: the app's own\n---\n\n{FACT}\n\nedited\n",
        encoding="utf-8",
    )
    with pytest.raises(MnemeError, match="chargebacks"):
        classify.review_finalize(home, repo, push=False)
