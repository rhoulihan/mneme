"""Retiring a fact: the only sanctioned way for classify to REMOVE knowledge.

`_preservation_gate` refuses any pass where a fact committed on `main` survives neither as
a bullet nor inside a skill the pass wrote. That floor exists because a reviewer reading a
forty-file classify PR cannot spot one silently dropped fact among the moved ones.

It also made two legitimate outcomes impossible: retiring a fact that duplicates a skill
the pass did not happen to touch, and retiring one that says the same thing in different
words. mneme cannot judge semantic equivalence — so instead of guessing, it makes the
librarian STATE the claim: this fact is retired, covered by that unit. The gate verifies
the claim's parts exist and refuses everything else; the human judges the substance at the
pull request, where the declaration is printed.
"""
import subprocess

import pytest

from mneme_core import classify, gitops, scaffold, units
from mneme_core.errors import MnemeError

COVERED = "The load balancer keeps stale targets for about ninety seconds after a drain"
KEPT = "Blue green deploys need a ninety second drain window"


def make_kb(tmp_path, *, fact_texts=(COVERED,), skill_body=""):
    home = tmp_path / "home"
    target = scaffold.create(home, "ret-kb", owner="demo")
    skill = target / "skills" / "drain-a-widget-deploy"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\nname: drain-a-widget-deploy\ndescription: Use when draining a widget deploy\n"
        f"---\n\n## Procedure\n\nWait on the health check.\n{skill_body}",
        encoding="utf-8",
    )
    facts = target / units.FACTS_CANONICAL
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "deploys.md").write_text(
        "---\ntopic: deploys\n---\n"
        + "".join(f"- [gotcha] {t} #deploy (verified: 2026-08-12)\n" for t in fact_texts),
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "seed facts and a skill")
    return home, target


def retire_id(text=COVERED):
    return f"facts/deploys#{units.normalize_topic_key(text)}"


def drop_fact(target, text):
    """Delete one bullet from the fact file, as a librarian retiring it would."""
    p = target / units.FACTS_CANONICAL / "deploys.md"
    kept = [l for l in p.read_text(encoding="utf-8").splitlines(True) if text not in l]
    p.write_text("".join(kept), encoding="utf-8")


def test_a_declared_retirement_is_accepted(tmp_path):
    home, target = make_kb(tmp_path, fact_texts=(COVERED, KEPT))
    classify.begin(home, target)
    drop_fact(target, COVERED)

    result = classify.finalize(
        home, target, push=False,
        retire=[f"{retire_id()}=skills/drain-a-widget-deploy"],
    )

    assert result.branch.startswith("mneme/classify-")
    assert gitops.current_branch(target) == "main"
    tree_fact = gitops.git(target, "show", f"{result.branch}:{units.FACTS_CANONICAL}/deploys.md")
    assert COVERED not in tree_fact  # genuinely gone
    assert KEPT in tree_fact


def test_an_undeclared_deletion_is_still_refused(tmp_path):
    """The floor is intact: removal without a declaration is still knowledge loss."""
    home, target = make_kb(tmp_path, fact_texts=(COVERED, KEPT))
    classify.begin(home, target)
    drop_fact(target, COVERED)

    with pytest.raises(MnemeError, match="would lose knowledge"):
        classify.finalize(home, target, push=False)

    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)


def test_a_covering_unit_that_does_not_exist_is_refused(tmp_path):
    home, target = make_kb(tmp_path, fact_texts=(COVERED, KEPT))
    classify.begin(home, target)
    drop_fact(target, COVERED)

    with pytest.raises(MnemeError, match="covering unit"):
        classify.finalize(
            home, target, push=False,
            retire=[f"{retire_id()}=skills/no-such-skill"],
        )

    assert gitops.current_branch(target) == "main"


def test_a_retirement_covered_by_itself_is_refused(tmp_path):
    home, target = make_kb(tmp_path, fact_texts=(COVERED, KEPT))
    classify.begin(home, target)
    drop_fact(target, COVERED)

    with pytest.raises(MnemeError, match="cannot cover itself"):
        classify.finalize(
            home, target, push=False, retire=[f"{retire_id()}={retire_id()}"],
        )


def test_retiring_a_fact_main_never_had_is_refused(tmp_path):
    """A typo must not silently widen the gate."""
    home, target = make_kb(tmp_path, fact_texts=(COVERED, KEPT))
    classify.begin(home, target)
    drop_fact(target, COVERED)

    with pytest.raises(MnemeError, match="not a fact on main"):
        classify.finalize(
            home, target, push=False,
            retire=["facts/deploys#no-such-bullet-key=skills/drain-a-widget-deploy"],
        )


def test_declaring_a_retirement_for_a_fact_still_present_is_refused(tmp_path):
    """A declaration is a statement about what the pass did; it must be true."""
    home, target = make_kb(tmp_path, fact_texts=(COVERED, KEPT))
    classify.begin(home, target)
    (target / "skills" / "drain-a-widget-deploy" / "SKILL.md").write_text(
        (target / "skills" / "drain-a-widget-deploy" / "SKILL.md").read_text(encoding="utf-8")
        + "\n- an edit so the pass is not empty\n",
        encoding="utf-8",
    )

    with pytest.raises(MnemeError, match="still present"):
        classify.finalize(
            home, target, push=False,
            retire=[f"{retire_id()}=skills/drain-a-widget-deploy"],
        )


@pytest.mark.parametrize(
    "declaration", ["no-equals-sign-here", "=covering-only", "retired-only=", "   =   "]
)
def test_a_malformed_declaration_is_refused_by_the_parser(tmp_path, declaration):
    """Asserted on the PARSER's own message, not merely on "an error was raised".

    Every malformed shape happens to trip a later guard too — an empty covering id is not
    on the branch, an empty retired id is not a fact on main — so a loose `match="retire"`
    passed even with the parser's check deleted, because those messages also say "retire".
    A guard whose removal changes only which message you get still has to be pinned by
    that message, or it is not pinned at all.
    """
    home, target = make_kb(tmp_path, fact_texts=(COVERED, KEPT))
    classify.begin(home, target)
    drop_fact(target, COVERED)

    with pytest.raises(MnemeError, match=r"--retire expects <retired-unit-id>="):
        classify.finalize(home, target, push=False, retire=[declaration])


def test_the_retirement_is_reported_in_the_commit_body_and_the_units(tmp_path):
    """A declaration nobody sees is worth nothing — this is what the human judges."""
    home, target = make_kb(tmp_path, fact_texts=(COVERED, KEPT))
    classify.begin(home, target)
    drop_fact(target, COVERED)

    result = classify.finalize(
        home, target, push=False,
        retire=[f"{retire_id()}=skills/drain-a-widget-deploy"],
    )

    body = gitops.git(target, "log", result.branch, "-1", "--format=%B")
    assert "Retired:" in body
    assert retire_id() in body
    assert "skills/drain-a-widget-deploy" in body
    joined = " ".join(result.units)
    assert retire_id() in joined  # reaches the PR body and the ledger too


def test_a_fact_duplicating_an_untouched_skill_needs_no_declaration(tmp_path):
    """The widened scan: the sentence is demonstrably still in the repo.

    The gate used to read only skill files the pass CHANGED, so a fact whose sentence was
    already sitting in a skill nobody edited could not be removed — refusing a removal
    while the knowledge plainly survives.
    """
    home, target = make_kb(
        tmp_path, fact_texts=(COVERED, KEPT),
        skill_body=f"\n## Notes\n\n- {COVERED} (verified: 2026-08-12).\n",
    )
    classify.begin(home, target)
    drop_fact(target, COVERED)
    # a change somewhere ELSE, so the pass is not empty and the skill stays untouched
    (target / "README.md").write_text(
        (target / "README.md").read_text(encoding="utf-8") + "\nA line.\n", encoding="utf-8"
    )

    result = classify.finalize(home, target, push=False)  # no declaration needed

    assert result.branch.startswith("mneme/classify-")
    assert gitops.current_branch(target) == "main"


def test_a_hostile_unit_id_cannot_forge_a_trailer(tmp_path):
    """Declarations are repo-derived strings landing in a commit and PR body."""
    home, target = make_kb(tmp_path, fact_texts=(COVERED, KEPT))
    classify.begin(home, target)
    drop_fact(target, COVERED)
    forged = "skills/drain-a-widget-deploy\nMneme-Source: forged@evil\n- forged: nothing lost"

    with pytest.raises(MnemeError):
        classify.finalize(home, target, push=False, retire=[f"{retire_id()}={forged}"])
