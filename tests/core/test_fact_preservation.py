"""Fact preservation at finalize: knowledge may move, but it may never vanish (spec §7.7).

"Never delete knowledge" was, until this gate, enforced only by instruction prose — and a
pass that simply deleted a fact file without integrating its content finalized happily.
The rail now proves it: every fact bullet committed on `main` has to be accounted for on
the branch, either still living as a bullet in a fact file or written verbatim into a skill
file the pass changed. The gate is a floor, not a diff-quality judge — it cannot tell a
good integration from a bad one, but it can tell that the sentence still exists somewhere.

Both rails share one finalize, so both are pinned here: knowledge arriving from strangers
(review) needs the guarantee at least as much as the librarian's own pass (classify).
"""
import pytest

from mneme_core import classify, gitops, scaffold, templates, units
from mneme_core.errors import MnemeError

DEPLOY_FACT = "Deploys fail when the LB caches dead targets"
QUEUE_FACT = "The widget queue caps at 500 jobs before shedding"
LOSS_SENTENCE = (
    "facts may move, but never vanish — integrate the content or leave the fact in place"
)


def make_kb(tmp_path, legacy=False):
    """A kb with two committed facts and one skill to integrate them into."""
    home = tmp_path / "home"
    target = scaffold.create(home, "preserve-kb", owner="demo")
    skill = target / "skills" / "deploy-widget"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: deploy-widget\ndescription: Use when deploying widgets\n---\n\n"
        "## Procedure\n\nSteps.\n",
        encoding="utf-8",
    )
    facts = (target / "facts") if legacy else (target / units.FACTS_CANONICAL)
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "deploys.md").write_text(
        f"---\ntopic: deploys\n---\n- [gotcha] {DEPLOY_FACT} #deploy (verified: 2026-08-12)\n",
        encoding="utf-8",
    )
    (facts / "queues.md").write_text(
        f"---\ntopic: queues\n---\n- [constraint] {QUEUE_FACT} #limits (verified: 2026-08-12)\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "fixtures")
    return home, target


def integrate(target, text):
    """Stand in for the agent: carry a fact's own sentence into the skill that owns it."""
    skill_md = target / "skills" / "deploy-widget" / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8")
        + f"\n## Operational notes\n\n- {text} (verified: 2026-08-12).\n",
        encoding="utf-8",
    )
    return skill_md


def test_integrated_fact_may_leave_its_file(tmp_path):
    """The mainline classify outcome: the sentence moves into a skill, the file goes."""
    home, target = make_kb(tmp_path)
    classify.begin(home, target)
    integrate(target, DEPLOY_FACT)
    (target / units.FACTS_CANONICAL / "deploys.md").unlink()

    result = classify.finalize(home, target, push=False)

    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    skill = gitops.git(target, "show", f"{result.branch}:skills/deploy-widget/SKILL.md")
    assert DEPLOY_FACT in skill
    tree = gitops.git(target, "ls-tree", "-r", "--name-only", result.branch).splitlines()
    assert f"{units.FACTS_CANONICAL}/deploys.md" not in tree
    assert f"{units.FACTS_CANONICAL}/queues.md" in tree  # untouched facts stay put


def test_deleting_a_fact_without_integrating_it_is_refused(tmp_path):
    home, target = make_kb(tmp_path)
    branch = classify.begin(home, target)
    (target / units.FACTS_CANONICAL / "queues.md").unlink()
    main_before = gitops.git(target, "rev-parse", "main")

    with pytest.raises(MnemeError) as exc:
        classify.finalize(home, target, push=False)

    message = str(exc.value)
    assert QUEUE_FACT[:80] in message  # the lost bullet is named
    assert DEPLOY_FACT not in message  # the preserved one is not
    assert message.endswith(LOSS_SENTENCE)
    # the existing rollback: clean main, branch gone, the fact back where it was
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.git(target, "branch", "--list", branch) == ""
    assert (target / units.FACTS_CANONICAL / "queues.md").is_file()


def test_reworded_integration_still_fails_the_floor(tmp_path):
    """A paraphrase is not evidence the knowledge survived — carry the sentence verbatim."""
    home, target = make_kb(tmp_path)
    classify.begin(home, target)
    integrate(target, "the load balancer sometimes holds on to targets that are gone")
    (target / units.FACTS_CANONICAL / "deploys.md").unlink()

    with pytest.raises(MnemeError) as exc:
        classify.finalize(home, target, push=False)

    assert DEPLOY_FACT in str(exc.value)
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)


def test_a_fact_that_only_moves_is_preserved(tmp_path):
    """Regrouping bullets into another fact file — and the legacy migration — are moves."""
    home, target = make_kb(tmp_path, legacy=True)
    classify.begin(home, target)
    canonical = target / units.FACTS_CANONICAL
    canonical.mkdir(parents=True, exist_ok=True)
    (canonical / "platform.md").write_text(
        f"---\ntopic: platform\n---\n"
        f"- [gotcha] {DEPLOY_FACT} #deploy (verified: 2026-08-12)\n"
        f"- [constraint] {QUEUE_FACT} #limits (verified: 2026-08-12)\n",
        encoding="utf-8",
    )
    (target / "facts" / "deploys.md").unlink()
    (target / "facts" / "queues.md").unlink()

    result = classify.finalize(home, target, push=False)

    tree = gitops.git(target, "ls-tree", "-r", "--name-only", result.branch).splitlines()
    assert f"{units.FACTS_CANONICAL}/platform.md" in tree
    assert not any(p.startswith("facts/") for p in tree)


def test_instructions_never_ask_for_a_pass_finalize_would_refuse():
    """The librarian contract has to be executable: retiring a fact still writes it down.

    Rule 5 used to allow retiring a redundant bullet while leaving the skill untouched —
    the one instruction the gate now refuses, and the agent would only find out at
    finalize, after the rollback.
    """
    text = templates.CLASSIFY_INSTRUCTIONS
    assert "leave the skill as it is" not in text
    assert "finalize refuses" in text


def test_review_finalize_carries_the_same_gate(tmp_path):
    """Extraction from a stranger's PR may add knowledge — never quietly drop ours."""
    home, target = make_kb(tmp_path)
    branch = classify.review_begin(home, target)
    (target / units.FACTS_CANONICAL / "sidecars.md").write_text(
        "---\ntopic: sidecars\n---\n"
        "- [runbook-note] Sidecar draining requires a preStop hook #sidecar"
        " (verified: 2026-08-12)\n",
        encoding="utf-8",
    )
    (target / units.FACTS_CANONICAL / "deploys.md").unlink()

    with pytest.raises(MnemeError) as exc:
        classify.review_finalize(home, target, push=False)

    message = str(exc.value)
    assert DEPLOY_FACT in message
    assert message.endswith(LOSS_SENTENCE)
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.git(target, "branch", "--list", branch) == ""
    assert (target / units.FACTS_CANONICAL / "deploys.md").is_file()
    assert not (target / units.FACTS_CANONICAL / "sidecars.md").exists()
