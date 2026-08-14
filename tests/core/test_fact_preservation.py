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
# The refusal now names the third honest ending too: a fact may be retired when another
# unit genuinely covers it, declared so the human sees what left and what replaced it.
LOSS_SENTENCE = (
    "facts may move, but never vanish — integrate the content, leave the fact in place,"
    " or retire it with `--retire <unit-id>=<covering-unit-id>` naming the unit that"
    " already says it"
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
    # The BRANCH SURVIVES a preservation refusal, and `main` is untouched. This gate is the
    # only place mneme tells a librarian to retry with `--retire`; aborting here destroyed
    # the edits that retry needs, so the advice could never be taken. It now refuses before
    # anything is touched, like every other declaration-shaped refusal.
    assert gitops.current_branch(target).startswith("mneme/")
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.git(target, "branch", "--list", branch) != ""  # the branch is still there
    # ...and so is the librarian's work: the refusal happens before anything is touched, so
    # there is nothing to undo. The deletion they made is still theirs to correct.
    assert not (target / units.FACTS_CANONICAL / "queues.md").is_file()


def test_reworded_integration_still_fails_the_floor(tmp_path):
    """A paraphrase is not evidence the knowledge survived — carry the sentence verbatim."""
    home, target = make_kb(tmp_path)
    classify.begin(home, target)
    integrate(target, "the load balancer sometimes holds on to targets that are gone")
    (target / units.FACTS_CANONICAL / "deploys.md").unlink()

    with pytest.raises(MnemeError) as exc:
        classify.finalize(home, target, push=False)

    assert DEPLOY_FACT in str(exc.value)
    assert gitops.current_branch(target).startswith("mneme/")  # branch survives


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


def test_de_bulleting_a_fact_in_place_is_refused(tmp_path):
    """The gate's own directory is not an integration destination.

    The audit's bypass: rewrite the bullet as prose *inside the fact file*. The file lives
    under `skills/`, so it counted as "a skill file the pass changed" and the sentence was
    'accounted for' — while `units.fact_files` + `parse_bullet_line` (lint, the index build,
    search, the classify bundle) no longer saw a bullet there at all.
    """
    home, target = make_kb(tmp_path)
    branch = classify.begin(home, target)
    (target / units.FACTS_CANONICAL / "deploys.md").write_text(
        f"---\ntopic: deploys\n---\nretired note: {DEPLOY_FACT} (was #deploy)\n",
        encoding="utf-8",
    )

    with pytest.raises(MnemeError) as exc:
        classify.finalize(home, target, push=False)

    message = str(exc.value)
    assert DEPLOY_FACT[:80] in message
    assert message.endswith(LOSS_SENTENCE)
    assert gitops.current_branch(target).startswith("mneme/")  # branch survives
    assert gitops.git(target, "branch", "--list", branch) != ""  # the branch is still there
    # The librarian's prose rewrite is left in place for them to fix — `main` still carries
    # the bullet, which is what the refusal is protecting.
    text = gitops.git(target, "show", f"main:{units.FACTS_CANONICAL}/deploys.md")
    assert f"- [gotcha] {DEPLOY_FACT}" in text


def test_moving_a_fact_into_a_facts_subdirectory_is_refused(tmp_path):
    """`units.fact_files` globs `*.md` — one directory deep is out of every reader's sight."""
    home, target = make_kb(tmp_path)
    classify.begin(home, target)
    facts = target / units.FACTS_CANONICAL
    archive = facts / "archive"
    archive.mkdir()
    (archive / "deploys.md").write_text(
        (facts / "deploys.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (facts / "deploys.md").unlink()

    with pytest.raises(MnemeError) as exc:
        classify.finalize(home, target, push=False)

    assert DEPLOY_FACT[:80] in str(exc.value)
    assert gitops.current_branch(target).startswith("mneme/")  # branch survives
    # The move the librarian made is still on the branch — refused, not reverted — while
    # `main` keeps the bullet the gate exists to protect.
    assert (facts / "archive").exists()
    assert DEPLOY_FACT in gitops.git(target, "show", f"main:{units.FACTS_CANONICAL}/deploys.md")


def test_the_legacy_migration_cannot_launder_a_hidden_fact(tmp_path):
    """Same bypass through the legacy layout: the migration lifts `facts/archive/` too.

    `_migrate_legacy_facts` runs before the gate, so a fact tucked into a subdirectory of
    the top-level layout arrives under `skills/` — and used to be read as integration text.
    """
    home, target = make_kb(tmp_path, legacy=True)
    classify.begin(home, target)
    legacy = target / "facts"
    (legacy / "archive").mkdir()
    (legacy / "archive" / "deploys.md").write_text(
        (legacy / "deploys.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (legacy / "deploys.md").unlink()

    with pytest.raises(MnemeError) as exc:
        classify.finalize(home, target, push=False)

    assert DEPLOY_FACT[:80] in str(exc.value)
    assert gitops.current_branch(target).startswith("mneme/")  # branch survives
    # Refused before the migration ran, so the librarian's move is still where they left it
    # and `main` still carries the bullet the gate is protecting.
    assert (legacy / "archive" / "deploys.md").is_file()
    assert DEPLOY_FACT in gitops.git(target, "show", "main:facts/deploys.md")


def test_review_finalize_refuses_a_de_bulleted_fact_too(tmp_path):
    """The rail is shared, so extraction from a stranger's PR cannot de-bullet ours."""
    home, target = make_kb(tmp_path)
    classify.review_begin(home, target)
    (target / units.FACTS_CANONICAL / "queues.md").write_text(
        f"---\ntopic: queues\n---\nnote: {QUEUE_FACT}\n", encoding="utf-8"
    )

    with pytest.raises(MnemeError) as exc:
        classify.review_finalize(home, target, push=False)

    assert QUEUE_FACT[:80] in str(exc.value)
    assert gitops.current_branch(target).startswith("mneme/")  # branch survives


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
    assert gitops.current_branch(target).startswith("mneme/")  # branch survives
    assert gitops.git(target, "branch", "--list", branch) != ""
    # `main` is what the gate protects, and it is untouched.
    assert DEPLOY_FACT in gitops.git(target, "show", f"main:{units.FACTS_CANONICAL}/deploys.md")
