import json

import pytest

from mneme_core import classify, gitops, paths, scaffold, units
from mneme_core.cli import main
from mneme_core.errors import MnemeError


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_kb(tmp_path, legacy=False):
    home = tmp_path / "home"
    target = scaffold.create(home, "lib-kb", owner="demo")
    skill = target / "skills" / "deploy-widget"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: deploy-widget\ndescription: Use when deploying widgets\n---\n\n## Procedure\n\nSteps.\n",
        encoding="utf-8",
    )
    facts = (target / "facts") if legacy else (target / units.FACTS_CANONICAL)
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "deploys.md").write_text(
        "---\ntopic: deploys\n---\n"
        "- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-12)\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "fixtures")
    return home, target


def integrate(target):
    """Stand in for the agent: fold the fixture fact into the fixture skill."""
    skill_md = target / "skills" / "deploy-widget" / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8")
        + "\n## Operational notes\n\n- Deploys fail when the LB caches dead targets (verified: 2026-08-12).\n",
        encoding="utf-8",
    )
    return skill_md


def test_bundle_shape(tmp_path, capsys):
    home, target = make_kb(tmp_path)
    b = classify.bundle(home, target)
    assert b["plugin"] == "lib-kb"
    assert b["legacy_layout"] is False
    fact = b["facts"][0]
    assert fact["unit_id"] == "facts/deploys#deploys-fail-when-the-lb-caches"
    assert fact["category"] == "gotcha"
    names = [s["name"] for s in b["skills"]]
    assert "deploy-widget" in names
    assert "knowledge-index" not in names
    assert "NEVER delete" in b["instructions"] or "never delete" in b["instructions"].lower()
    code, out, _ = run(capsys, "--home", str(home), "classify", "prepare", "--cwd", str(target / "skills"))
    assert code == 0
    assert json.loads(out)["plugin"] == "lib-kb"


def test_finalize_full_pass_with_migration(tmp_path):
    home, target = make_kb(tmp_path, legacy=True)
    classify.begin(home, target)
    # simulate the agent integrating the fact into the skill
    skill_md = target / "skills" / "deploy-widget" / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8")
        + "\n## Operational notes\n\n- Deploys fail when the LB caches dead targets (verified: 2026-08-12).\n",
        encoding="utf-8",
    )
    (target / "facts" / "deploys.md").unlink()
    main_before = gitops.git(target, "rev-parse", "main")
    result = classify.finalize(home, target, push=False)
    assert result.branch.startswith("mneme/classify-")
    assert gitops.git(target, "rev-parse", "main") == main_before  # PR-only invariant
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    subject = gitops.git(target, "log", result.branch, "-1", "--format=%s")
    assert subject.startswith("knowledge: classify")
    # legacy dir migrated on the branch
    tree = gitops.git(target, "ls-tree", "-r", "--name-only", result.branch)
    assert not any(p.startswith("facts/") for p in tree.splitlines())


def test_finalize_requires_active_branch_and_changes(tmp_path):
    home, target = make_kb(tmp_path)
    with pytest.raises(MnemeError):
        classify.finalize(home, target, push=False)
    classify.begin(home, target)
    with pytest.raises(MnemeError):
        classify.finalize(home, target, push=False)  # no edits, no migration
    assert gitops.current_branch(target) == "main"  # rolled back cleanly


def test_finalize_gate_rolls_back_on_lint_error(tmp_path):
    home, target = make_kb(tmp_path)
    classify.begin(home, target)
    bad = target / "skills" / "broken-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: Wrong_Name\n---\n", encoding="utf-8")
    main_before = gitops.git(target, "rev-parse", "main")
    with pytest.raises(MnemeError):
        classify.finalize(home, target, push=False)
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.git(target, "rev-parse", "main") == main_before


def test_finalize_migrates_surviving_legacy_facts(tmp_path):
    """A fact with no better home stays a fact — but it still moves to the new location."""
    home, target = make_kb(tmp_path, legacy=True)
    (target / "facts" / "keepers.md").write_text(
        "---\ntopic: keepers\n---\n"
        "- [constraint] The widget queue caps at 500 jobs #limits (verified: 2026-08-12)\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "second legacy fact")
    classify.begin(home, target)
    integrate(target)
    (target / "facts" / "deploys.md").unlink()

    result = classify.finalize(home, target, push=False)

    tree = gitops.git(target, "ls-tree", "-r", "--name-only", result.branch).splitlines()
    assert "skills/knowledge-index/facts/keepers.md" in tree
    assert not any(p.startswith("facts/") for p in tree)
    # git-level rename, so the fact's history follows it
    assert "keepers.md" in gitops.git(
        target, "show", "--name-status", "--format=", "-M", result.branch
    )
    # the regenerated index routes to the migrated file and has dropped the retired topic
    index_md = gitops.git(target, "show", f"{result.branch}:skills/knowledge-index/SKILL.md")
    assert "facts/keepers.md" in index_md
    assert "deploys" not in index_md
    body = gitops.git(target, "log", result.branch, "-1", "--format=%b")
    assert "skills/knowledge-index/facts/keepers.md" in body


def test_changed_paths_are_reported_byte_for_byte(tmp_path):
    """The commit body names files exactly as git does — the first record included.

    `git status --porcelain` opens an unstaged record with a space, and the first record
    is where any trimming of git's output shows up: a path one byte short here would also
    be a file the secret-scan gate silently skipped.
    """
    home, target = make_kb(tmp_path)
    classify.begin(home, target)
    integrate(target)
    (target / units.FACTS_CANONICAL / "deploys.md").unlink()
    result = classify.finalize(home, target, push=False)
    body = gitops.git(target, "log", result.branch, "-1", "--format=%b")
    assert "- skills/deploy-widget/SKILL.md" in body
    assert "- skills/knowledge-index/facts/deploys.md" in body
    assert "- kills/" not in body


def test_cli_finalize_reports_branch_and_records_ledger(tmp_path, capsys):
    home, target = make_kb(tmp_path)
    run(capsys, "--home", str(home), "classify", "begin", "--cwd", str(target))
    integrate(target)
    (target / units.FACTS_CANONICAL / "deploys.md").unlink()
    code, out, _ = run(
        capsys, "--home", str(home), "classify", "finalize",
        "--cwd", str(target / "skills"), "--no-push",
    )
    assert code == 0
    assert "mneme/classify-" in out
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    record = json.loads(
        paths.submitted_path(home).read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert record["kind"] == "classify"
    assert record["target"] == "lib-kb"
    assert record["branch"].startswith("mneme/classify-")
