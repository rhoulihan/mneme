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

    # The BRANCH SURVIVES a rejected declaration, and so does the work on it. Validation
    # runs before the guarded block for exactly this reason: it was inside, so one mistyped
    # unit id ran `_abort` — reset --hard, checkout main, branch -D — and destroyed a
    # librarian's committed reorganisation while telling them to check the id and retry.
    assert gitops.current_branch(target).startswith("mneme/classify-")
    assert target / units.FACTS_CANONICAL / "deploys.md"


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


def test_a_fact_duplicating_an_untouched_skill_still_needs_a_declaration(tmp_path):
    """The coverage scan reads only files this pass CHANGED — deliberately, again.

    It was briefly widened to the whole skill tree so this case would pass with no
    declaration. The justification was that the match is a whole sentence; it is a
    SUBSTRING against every skill flattened into one line, so the widened form retired
    facts by a four-word prefix of unrelated prose, by a heading joined to the body beneath
    it, and by an anti-pattern section quoting a fact in order to refute it. The honest
    answer to this case is the declaration: say which unit covers it, and let a human read
    that in the pull request.
    """
    home, target = make_kb(
        tmp_path, fact_texts=(COVERED, KEPT),
        skill_body=f"\n## Notes\n\n- {COVERED} (verified: 2026-08-12).\n",
    )
    classify.begin(home, target)
    drop_fact(target, COVERED)
    (target / "README.md").write_text(
        (target / "README.md").read_text(encoding="utf-8") + "\nA line.\n", encoding="utf-8"
    )

    with pytest.raises(MnemeError, match="would lose knowledge"):
        classify.finalize(home, target, push=False)

    # That IS a gate failure, so the branch rolls back — unlike a rejected declaration,
    # which is caught before anything is touched.
    assert gitops.current_branch(target) == "main"

    # ...and the declaration is what makes the same pass legitimate.
    classify.begin(home, target)
    drop_fact(target, COVERED)
    (target / "README.md").write_text(
        (target / "README.md").read_text(encoding="utf-8") + "\nA line.\n", encoding="utf-8"
    )
    result = classify.finalize(
        home, target, push=False,
        retire=[f"{retire_id()}=skills/drain-a-widget-deploy"],
    )
    assert any(u.startswith("Retired:") for u in result.units)


def test_one_declaration_does_not_launder_a_second_undeclared_deletion(tmp_path):
    """Each fact is accounted for INDIVIDUALLY.

    Every other test here drops one fact and declares that one, so the per-fact match was
    unpinned: changing the gate's `_normalized(text) not in retired` to `not retired` left
    the whole suite green while one valid declaration excused every other deletion in the
    pass — facts leaving with nothing said about them, which is the thing this gate exists
    to prevent.
    """
    second = "Canary deploys hold ten percent of traffic for a full hour"
    home, target = make_kb(tmp_path, fact_texts=(COVERED, second, KEPT))
    classify.begin(home, target)
    drop_fact(target, COVERED)
    drop_fact(target, second)  # dropped, and NOT declared

    with pytest.raises(MnemeError, match="would lose knowledge") as exc:
        classify.finalize(
            home, target, push=False,
            retire=[f"{retire_id()}=skills/drain-a-widget-deploy"],
        )

    assert second[:60] in str(exc.value)          # the undeclared one is named
    assert COVERED[:60] not in str(exc.value)     # the declared one is not


def test_an_ambiguous_unit_id_is_refused_rather_than_guessed_at(tmp_path):
    """A unit id is the first SIX words of a sentence, so it can name several bullets.

    Two bullets in one topic file sharing an opening phrase is routine — every bullet in
    it is about the same subject. Excusing by id retired all of them while naming one in
    the pull request.
    """
    twin_a = "The load balancer keeps stale targets for about ninety seconds after a drain"
    twin_b = "The load balancer keeps stale targets in the DNS cache for a full hour"
    assert units.normalize_topic_key(twin_a) == units.normalize_topic_key(twin_b)
    home, target = make_kb(tmp_path, fact_texts=(twin_a, twin_b, KEPT))
    classify.begin(home, target)
    drop_fact(target, twin_a)
    drop_fact(target, twin_b)

    with pytest.raises(MnemeError, match="names 2 different bullets on main"):
        classify.finalize(
            home, target, push=False,
            retire=[f"{retire_id(twin_a)}=skills/drain-a-widget-deploy"],
        )


def test_a_covering_unit_cannot_itself_be_retired(tmp_path):
    """No cycles, and no retiring into something that is also leaving."""
    other = "Canary deploys hold ten percent of traffic for a full hour"
    home, target = make_kb(tmp_path, fact_texts=(COVERED, other, KEPT))
    classify.begin(home, target)
    drop_fact(target, COVERED)
    drop_fact(target, other)

    with pytest.raises(MnemeError, match="is itself being retired"):
        classify.finalize(
            home, target, push=False,
            retire=[
                f"{retire_id(COVERED)}={retire_id(other)}",
                f"{retire_id(other)}=skills/drain-a-widget-deploy",
            ],
        )


def test_the_retirement_leads_the_body_and_is_never_truncated(tmp_path):
    """`bound_body` truncates from the END, so ordering here is load-bearing.

    With retirements appended last, a wide pass dropped the only line saying a fact was
    deleted — out of the commit body, the PR body, and the ledger, which stores this same
    bounded list.
    """
    home, target = make_kb(tmp_path, fact_texts=(COVERED, KEPT))
    classify.begin(home, target)
    drop_fact(target, COVERED)
    for i in range(40):  # plenty of other changed paths competing for the budget
        (target / "skills" / "drain-a-widget-deploy" / f"note-{i:02d}.md").write_text(
            f"# note {i}\n\nfiller\n", encoding="utf-8"
        )

    result = classify.finalize(
        home, target, push=False,
        retire=[f"{retire_id()}=skills/drain-a-widget-deploy"],
    )

    assert result.units[0].startswith("Retired:")
    body = gitops.git(target, "log", result.branch, "-1", "--format=%B")
    assert "Retired:" in body


def test_a_hostile_unit_id_reaching_the_body_is_flattened(tmp_path):
    """The forgery guard, exercised on an ACCEPTED declaration.

    The previous version of this test used a hostile COVERING id, which the
    covering-unit-exists check rejects before `layout._safe` is ever reached — so it passed
    with every `_safe` call deleted. The id that actually reaches the body comes from a
    fact file's own stem, which is repo content: a stem carrying newlines splices a forged
    trailer and an invented bullet into the commit and pull request body.
    """
    home, target = make_kb(tmp_path, fact_texts=(KEPT,))
    evil_stem = "dep\nMneme-Source: forged@evil\n- forged: nothing was lost"
    hostile = "The alpha service retries three times before failing over to the replica"
    (target / units.FACTS_CANONICAL / f"{evil_stem}.md").write_text(
        f"---\ntopic: alpha\n---\n- [gotcha] {hostile} #x (verified: 2026-08-12)\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "a fact file with a hostile stem")
    classify.begin(home, target)
    (target / units.FACTS_CANONICAL / f"{evil_stem}.md").unlink()

    result = classify.finalize(
        home, target, push=False,
        retire=[
            f"facts/{evil_stem}#{units.normalize_topic_key(hostile)}"
            "=skills/drain-a-widget-deploy"
        ],
    )

    body = gitops.git(target, "log", result.branch, "-1", "--format=%B")
    assert not any(l.startswith("Mneme-Source: forged@evil") for l in body.splitlines())
    assert not any(l.startswith("- forged:") for l in body.splitlines())
    assert all(len(u.splitlines()) <= 1 for u in result.units)


def test_review_finalize_accepts_declarations_too(tmp_path):
    """The pass-through was untested; dropping `retire=retire` left the suite green."""
    home, target = make_kb(tmp_path, fact_texts=(COVERED, KEPT))
    classify._begin(home, target, "review")
    drop_fact(target, COVERED)

    result = classify.review_finalize(
        home, target, push=False,
        retire=[f"{retire_id()}=skills/drain-a-widget-deploy"],
    )

    assert result.branch.startswith("mneme/review-")
    assert result.units[0].startswith("Retired:")


def test_the_cli_flag_reaches_the_rail_and_repeats(tmp_path, capsys):
    """The flag is the only way a human or librarian reaches this feature, and it had none.

    Two mutations left the whole suite green: dropping `retire=args.retire` from the call,
    so every declared retirement is refused as an undeclared loss; and dropping
    `action="append"`, so a second declaration silently replaces the first.
    """
    from mneme_core.cli import main

    other = "Canary deploys hold ten percent of traffic for a full hour"
    home, target = make_kb(tmp_path, fact_texts=(COVERED, other, KEPT))
    main(["--home", str(home), "classify", "begin", "--cwd", str(target)])
    drop_fact(target, COVERED)
    drop_fact(target, other)

    code = main([
        "--home", str(home), "classify", "finalize", "--cwd", str(target), "--no-push",
        "--retire", f"{retire_id(COVERED)}=skills/drain-a-widget-deploy",
        "--retire", f"{retire_id(other)}=skills/drain-a-widget-deploy",
    ])
    out = capsys.readouterr().out

    assert code == 0, out
    branch = out.split(" on ")[1].split()[0]
    body = gitops.git(target, "log", branch, "-1", "--format=%B")
    assert body.count("Retired:") == 2  # BOTH declarations took effect


def test_the_instructions_print_the_flag_the_parser_actually_accepts(tmp_path):
    """Instruction/CLI drift is only discovered at finalize, after the rollback.

    The librarian executes this text verbatim. Renaming the flag or its separator in the
    instructions left the suite green while making every retirement impossible.
    """
    from mneme_core import templates
    from mneme_core.cli import _build_parser

    assert f"--retire <retired-unit-id>{classify._RETIRE_SEP}<covering-unit-id>" in (
        templates.CLASSIFY_INSTRUCTIONS
    )
    parser = _build_parser()
    args = parser.parse_args(["classify", "finalize", "--retire", "a=b", "--retire", "c=d"])
    assert args.retire == ["a=b", "c=d"]  # the spelled flag parses, and repeats
    with pytest.raises(MnemeError, match="unrecognized arguments"):
        parser.parse_args(["classify", "finalize", "--drop", "a:b"])


def test_no_retirement_is_ever_truncated_out_of_the_body(tmp_path, monkeypatch):
    """Retirements are reserved OUT of the budget, not merely placed first in it.

    Ordering alone is not enough. `bound_body` truncates from the end, so folding the
    retirements into the bounded list keeps them only while they are small: once they take
    more than the remaining budget they start being dropped, and the line saying a fact was
    deleted disappears from the commit body, the PR body and the ledger record — which
    stores this same bounded list. Pinned with a small budget rather than 25 KB of
    fixtures, so the property is tested rather than approximated.
    """
    from mneme_core import layout

    facts = [f"Retiring fact number {i:02d} about the widget platform deploy path" for i in range(8)]
    home, target = make_kb(tmp_path, fact_texts=(*facts, KEPT))
    classify.begin(home, target)
    for text in facts:
        drop_fact(target, text)
    for i in range(30):  # changed paths competing for the same body
        (target / "skills" / "drain-a-widget-deploy" / f"n{i:02d}.md").write_text(
            f"# n{i}\n\n{'filler ' * 40}\n", encoding="utf-8"
        )
    monkeypatch.setattr(layout, "_BODY_MAX", 1200)

    result = classify.finalize(
        home, target, push=False,
        retire=[f"{retire_id(t)}=skills/drain-a-widget-deploy" for t in facts],
    )

    retired_lines = [u for u in result.units if u.startswith("Retired:")]
    assert len(retired_lines) == len(facts)          # every one survived the bound
    assert result.units[: len(facts)] == retired_lines  # and they lead
    assert any("omitted" in u for u in result.units)    # something WAS truncated
    body = gitops.git(target, "log", result.branch, "-1", "--format=%B")
    assert body.count("Retired:") == len(facts)


def test_retirements_that_cannot_fit_refuse_the_pass(tmp_path, monkeypatch):
    """A retirement that is not reported is a fact deleted in silence — so refuse instead."""
    from mneme_core import layout

    facts = [f"Retiring fact number {i:02d} about the widget platform deploy path" for i in range(8)]
    home, target = make_kb(tmp_path, fact_texts=(*facts, KEPT))
    classify.begin(home, target)
    for text in facts:
        drop_fact(target, text)
    monkeypatch.setattr(layout, "_BODY_MAX", 120)  # smaller than the declarations alone

    with pytest.raises(MnemeError, match="do not fit in one pull request body"):
        classify.finalize(
            home, target, push=False,
            retire=[f"{retire_id(t)}=skills/drain-a-widget-deploy" for t in facts],
        )

    # Refused BEFORE anything is touched, like every other declaration refusal — the
    # branch and the librarian's work on it survive. Raising this where the body is
    # assembled put it past the guarded block, leaving a half-migrated branch behind.
    assert gitops.current_branch(target).startswith("mneme/classify-")
