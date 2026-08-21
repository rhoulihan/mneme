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
    # The bullet's own words, not `FACT.split()[0]` — which is "The", and matches any
    # English sentence — and not the path, which the line above already covers.
    assert "chargebacks" in str(e.value)
    assert "seventy two hours" in str(e.value), str(e.value)


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


# --- the gate's proofs must all ask git, and none may vote on its own mode ----


def test_an_integration_proved_by_a_symlink_does_not_count(tmp_path):
    """The gate's FOURTH proof. Git commits the link's target STRING, not the bytes.

    `_branch_fact_texts` and `_branch_unit_ids` were both taught to ask `_committable`;
    `_integration_text` sat ten lines away still asking the disk, so a `SKILL.md` sibling
    symlinked at a file outside the repo satisfied the integration proof while the sentence
    appeared in zero committed files.
    """
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    skill = target / "skills" / "drain-a-widget-deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: drain-a-widget-deploy\ndescription: Use when draining a deploy\n---\nBody\n",
        encoding="utf-8",
    )
    facts = target / units.FACTS_CANONICAL
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "deploys.md").write_text(
        f"---\ntopic: deploys\n---\n- [gotcha] {FACT} #deploy (verified: 2026-08-14)\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "seed")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.md").write_text(f"Integrated here: {FACT}\n", encoding="utf-8")

    classify.begin(home, target)
    (facts / "deploys.md").write_text("---\ntopic: deploys\n---\n", encoding="utf-8")
    (skill / "notes.md").symlink_to(outside / "notes.md")

    with pytest.raises(MnemeError, match="deploys"):
        classify.finalize(home, target, push=False)


def test_a_gitignored_integration_does_not_count_either(tmp_path):
    """Same proof, the other way a file fails to reach the commit."""
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    skill = target / "skills" / "drain-a-widget-deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: drain-a-widget-deploy\ndescription: Use when draining a deploy\n---\nBody\n",
        encoding="utf-8",
    )
    facts = target / units.FACTS_CANONICAL
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "deploys.md").write_text(
        f"---\ntopic: deploys\n---\n- [gotcha] {FACT} #deploy (verified: 2026-08-14)\n",
        encoding="utf-8",
    )
    (target / ".gitignore").write_text("notes.md\n", encoding="utf-8")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "seed")

    classify.begin(home, target)
    (facts / "deploys.md").write_text("---\ntopic: deploys\n---\n", encoding="utf-8")
    (skill / "notes.md").write_text(f"Integrated here: {FACT}\n", encoding="utf-8")

    with pytest.raises(MnemeError, match="deploys"):
        classify.finalize(home, target, push=False)


def test_a_pass_cannot_vote_itself_plugin_powers(tmp_path):
    """The mode check must read `main` — the one ref PR-only guarantees the pass cannot edit.

    Reading `units.is_plugin` off the working tree let a pass write the very manifest it was
    being judged by: add `.claude-plugin/plugin.json` and `_carries_knowledge` flips to True
    for the application's own `skills/`, so a fact deleted from `mneme-index/facts/` is
    suddenly accounted for by a file mneme did not write.
    """
    home, repo = seeded_app(tmp_path)
    (repo / "skills" / "combat").mkdir(parents=True)
    (repo / "skills" / "combat" / "SKILL.md").write_text(
        f"---\nname: combat\ndescription: the app's own\n---\n\n{FACT}\n", encoding="utf-8"
    )
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "the app's own skill")

    classify.review_begin(home, repo)
    (repo / units.FACTS_PLAIN / "chargebacks.md").write_text(
        "---\ntopic: chargebacks\n---\n", encoding="utf-8"
    )
    # The pass promotes the repo to a plugin mid-flight, then edits the app's skill so it
    # counts as "changed" and would be scanned as an integration destination.
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "payments", "version": "0.1.0"}\n', encoding="utf-8"
    )
    (repo / "skills" / "combat" / "SKILL.md").write_text(
        f"---\nname: combat\ndescription: the app's own\n---\n\n{FACT}\n\nedited\n",
        encoding="utf-8",
    )

    with pytest.raises(MnemeError, match="chargebacks"):
        classify.review_finalize(home, repo, push=False)


def test_the_preservation_refusal_leaves_the_branch_to_fix(tmp_path):
    """The gate runs BEFORE the guarded block for one reason: so the advice is actionable.

    Both placements raise the same sentence — including the "retry with `--retire`" hint —
    so a test that only asserts `raises` cannot tell them apart. The one inside the guard
    hard-resets and deletes the branch first, and then tells the librarian to retry work
    that no longer exists.
    """
    home, repo = seeded_app(tmp_path)
    branch = classify.review_begin(home, repo)
    (repo / units.FACTS_PLAIN / "chargebacks.md").write_text(
        "---\ntopic: chargebacks\n---\n", encoding="utf-8"
    )

    with pytest.raises(MnemeError, match="chargebacks"):
        classify.review_finalize(home, repo, push=False)

    assert gitops.current_branch(repo) == branch, "the pass's own work was discarded"
    assert branch in gitops.git(repo, "branch", "--list", branch)
    assert (repo / units.FACTS_PLAIN / "chargebacks.md").read_text("utf-8").endswith("---\n")


def test_the_refusal_names_the_bullet_that_would_be_lost(tmp_path):
    """Not just the file. A reviewer needs to know WHICH sentence went missing."""
    home, repo = seeded_app(tmp_path)
    classify.review_begin(home, repo)
    (repo / units.FACTS_PLAIN / "chargebacks.md").write_text(
        "---\ntopic: chargebacks\n---\n", encoding="utf-8"
    )
    with pytest.raises(MnemeError) as e:
        classify.review_finalize(home, repo, push=False)
    assert "seventy two hours" in str(e.value), str(e.value)


def test_a_pass_cannot_create_a_knowledge_root_to_gain_powers(tmp_path):
    """The manifest is not the only way to flip the mode mid-flight.

    `units.maintains_skills` answers "is mneme's own router under `skills/`", so a pass that
    WRITES `skills/knowledge-index/SKILL.md` makes the working tree say yes even though the
    base said no — and the application's own `skills/` becomes valid integration evidence
    for a fact the same pass just deleted. Only `main` can be trusted for this question, and
    only a repo with no established root on `main` can tell the two answers apart.
    """
    home, repo = plain_repo(tmp_path)
    legacy = repo / "facts"
    legacy.mkdir()
    (legacy / "chargebacks.md").write_text(
        f"---\ntopic: chargebacks\n---\n- [gotcha] {FACT} #cb (verified: 2026-08-14)\n",
        encoding="utf-8",
    )
    (repo / "skills" / "combat").mkdir(parents=True)
    (repo / "skills" / "combat" / "SKILL.md").write_text(
        f"---\nname: combat\ndescription: the app's own\n---\n\n{FACT}\n", encoding="utf-8"
    )
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "a legacy fact and the app's own skill")
    assert units.established_root(repo) is None, "the base has no knowledge root at all"

    classify.review_begin(home, repo)
    (legacy / "chargebacks.md").write_text("---\ntopic: chargebacks\n---\n", encoding="utf-8")
    # The pass installs a router under skills/, which would make `maintains_skills` true.
    (repo / units.PLUGIN_ROOT).mkdir(parents=True)
    (repo / units.PLUGIN_ROOT / "SKILL.md").write_text(
        "---\nname: knowledge-index\ndescription: router\n---\n", encoding="utf-8"
    )
    (repo / "skills" / "combat" / "SKILL.md").write_text(
        f"---\nname: combat\ndescription: the app's own\n---\n\n{FACT}\n\nedited\n",
        encoding="utf-8",
    )

    with pytest.raises(MnemeError, match="chargebacks"):
        classify.review_finalize(home, repo, push=False)


def test_review_never_credits_an_app_s_own_source_as_an_integration(tmp_path):
    """Triage's integration evidence had the plugin-only rule `_carries_knowledge` refuses.

    On a plain repo it walked the APPLICATION's `skills/`, so an inbound pull request's fact
    was labelled already-integrated because the app's own source happened to contain the
    sentence — and the maintainer drops a real contribution on the strength of a file mneme
    neither wrote nor reads for knowledge. Advisory rather than committed loss, which is
    exactly why nothing else catches it.
    """
    from mneme_core import review

    home, repo = seeded_app(tmp_path)
    (repo / "skills" / "combat").mkdir(parents=True)
    (repo / "skills" / "combat" / "SKILL.md").write_text(
        f"---\nname: combat\ndescription: the app's own\n---\n\n{FACT}\n", encoding="utf-8"
    )
    assert units.maintains_skills(repo) is False
    assert review._integrated_texts(repo) == set()
    assert not review._is_integrated(FACT, review._integrated_texts(repo))


def test_review_does_credit_an_integration_where_mneme_maintains_the_skills(tmp_path):
    """The same evidence, in a repo whose skills mneme owns, still counts."""
    from mneme_core import review

    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    skill = target / "skills" / "drain-a-widget-deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: drain-a-widget-deploy\ndescription: Use when draining\n---\n\n{FACT}\n",
        encoding="utf-8",
    )
    assert units.maintains_skills(target) is True
    assert review._is_integrated(FACT, review._integrated_texts(target))
