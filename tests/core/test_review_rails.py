"""Review rails: the classify rails run again under the `mneme/review-*` prefix (spec §7.8).

Extraction from a mixed pull request writes into the maintainer's own repo, so it needs
exactly the discipline classify already has — a branch of its own, every gate at finalize,
and `main` never written. Sharing the implementation is what makes that true by
construction; these tests pin the parts the prefix has to change (branch namespace, commit
subject, ledger kind) and the parts it must not (the gates, the rollback, PR-only).
"""
import json

import pytest

from mneme_core import classify, gitops, paths, scaffold, units
from mneme_core.cli import main
from mneme_core.errors import MnemeError

NEW_FACT = (
    "- [runbook-note] Sidecar draining requires a preStop hook #sidecar (verified: 2026-08-12)"
)


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_kb(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "review-kb", owner="demo")
    facts = target / units.FACTS_CANONICAL
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "deploys.md").write_text(
        "---\ntopic: deploys\n---\n"
        "- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-12)\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "fixtures")
    return home, target


def extract(target):
    """Stand in for the agent: write an approved PR bullet into the facts directory."""
    path = target / units.FACTS_CANONICAL / "sidecars.md"
    path.write_text(f"---\ntopic: sidecars\n---\n{NEW_FACT}\n", encoding="utf-8")
    return path


def test_begin_creates_a_review_branch(tmp_path):
    home, target = make_kb(tmp_path)
    branch = classify.review_begin(home, target / "skills")
    assert branch.startswith("mneme/review-")
    assert gitops.current_branch(target) == branch


def test_begin_guard_sees_both_prefixes(tmp_path):
    """One rail at a time: the two flows write the same repo, so either blocks the other."""
    home, target = make_kb(tmp_path)
    classify.review_begin(home, target)
    with pytest.raises(MnemeError) as exc:
        classify.begin(home, target)
    assert "already active" in str(exc.value)
    with pytest.raises(MnemeError):
        classify.review_begin(home, target)
    classify.review_abort(home, target)

    classify.begin(home, target)
    with pytest.raises(MnemeError) as exc:
        classify.review_begin(home, target)
    assert "already active" in str(exc.value)


def test_abort_restores_and_refuses_the_other_prefix(tmp_path):
    home, target = make_kb(tmp_path)
    branch = classify.review_begin(home, target)
    extract(target)
    # A review branch is not a classify branch: the classify rail must not delete it.
    with pytest.raises(MnemeError):
        classify.abort(home, target)
    assert gitops.current_branch(target) == branch

    classify.review_abort(home, target)
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.git(target, "branch", "--list", "mneme/review-*") == ""


def test_abort_outside_a_review_branch_refuses(tmp_path):
    home, target = make_kb(tmp_path)
    with pytest.raises(MnemeError):
        classify.review_abort(home, target)


def test_finalize_lands_the_extraction_on_the_review_branch(tmp_path):
    home, target = make_kb(tmp_path)
    classify.review_begin(home, target)
    extract(target)
    main_before = gitops.git(target, "rev-parse", "main")

    result = classify.review_finalize(home, target, push=False)

    assert result.branch.startswith("mneme/review-")
    assert gitops.git(target, "rev-parse", "main") == main_before  # PR-only invariant
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    subject = gitops.git(target, "log", result.branch, "-1", "--format=%s")
    assert subject.startswith("knowledge: review")
    tree = gitops.git(target, "ls-tree", "-r", "--name-only", result.branch).splitlines()
    assert f"{units.FACTS_CANONICAL}/sidecars.md" in tree
    # Every finalize gate still runs — the index regeneration among them.
    index_md = gitops.git(target, "show", f"{result.branch}:skills/knowledge-index/SKILL.md")
    assert "| sidecars |" in index_md
    assert "no remote" in result.pr


def test_finalize_requires_a_review_branch(tmp_path):
    home, target = make_kb(tmp_path)
    with pytest.raises(MnemeError) as exc:
        classify.review_finalize(home, target, push=False)
    assert "mneme review begin" in str(exc.value)
    classify.begin(home, target)
    with pytest.raises(MnemeError):
        classify.review_finalize(home, target, push=False)
    assert gitops.current_branch(target).startswith("mneme/classify-")  # not rolled back


def test_finalize_without_edits_discards_the_branch(tmp_path):
    home, target = make_kb(tmp_path)
    classify.review_begin(home, target)
    with pytest.raises(MnemeError) as exc:
        classify.review_finalize(home, target, push=False)
    assert "review" in str(exc.value)
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.git(target, "branch", "--list", "mneme/review-*") == ""


def test_finalize_gate_rolls_back_on_lint_error(tmp_path):
    home, target = make_kb(tmp_path)
    branch = classify.review_begin(home, target)
    extract(target)
    bad = target / "skills" / "broken-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: Wrong_Name\n---\n", encoding="utf-8")
    main_before = gitops.git(target, "rev-parse", "main")

    with pytest.raises(MnemeError):
        classify.review_finalize(home, target, push=False)

    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.git(target, "branch", "--list", branch) == ""
    assert not (target / "skills" / "broken-skill").exists()
    assert not (target / units.FACTS_CANONICAL / "sidecars.md").exists()


def test_classify_rail_is_untouched_by_the_generalization(tmp_path):
    """The shared helpers must not have moved classify's own branch, subject, or ledger."""
    home, target = make_kb(tmp_path)
    branch = classify.begin(home, target)
    assert branch.startswith("mneme/classify-")
    extract(target)
    result = classify.finalize(home, target, push=False)
    assert gitops.git(target, "log", result.branch, "-1", "--format=%s").startswith(
        "knowledge: classify"
    )
    record = json.loads(
        paths.submitted_path(home).read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert record["kind"] == "classify"


def test_cli_begin_finalize_records_a_review_ledger_entry(tmp_path, capsys):
    home, target = make_kb(tmp_path)
    code, out, _ = run(
        capsys, "--home", str(home), "review", "begin", "--cwd", str(target / "skills")
    )
    assert code == 0
    branch = out.strip()
    assert branch.startswith("mneme/review-")
    extract(target)

    code, out, _ = run(
        capsys, "--home", str(home), "review", "finalize", "--cwd", str(target), "--no-push"
    )

    assert code == 0
    assert branch in out
    assert "pr:" in out
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    record = json.loads(
        paths.submitted_path(home).read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert record["kind"] == "review"
    assert record["target"] == "review-kb"
    assert record["branch"] == branch
    assert f"{units.FACTS_CANONICAL}/sidecars.md" in record["units"]


def test_cli_abort_returns_to_main(tmp_path, capsys):
    home, target = make_kb(tmp_path)
    run(capsys, "--home", str(home), "review", "begin", "--cwd", str(target))
    extract(target)
    code, out, _ = run(capsys, "--home", str(home), "review", "abort", "--cwd", str(target))
    assert code == 0
    assert out.strip() == "aborted"
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)


def test_cli_outside_a_plugin_exits_one_with_message(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    code, _out, err = run(
        capsys, "--home", str(tmp_path / "h"), "review", "begin", "--cwd", str(plain)
    )
    assert code == 1
    assert "not inside a registered knowledge plugin" in err
