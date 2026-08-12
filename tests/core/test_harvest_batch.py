import json
import subprocess

import pytest

from mneme_core import compose, gitops, harvest, paths, registry, scaffold, staging
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id


def stage_skill(home, target="acme-knowledge", name="deploy-widget"):
    body = compose.render_skill_unit(
        name, "Use when deploying the widget service", "1. steps", "what failed",
        source="demo@s1", captured="2026-08-11",
    )
    cand = Candidate(
        id=candidate_id("skill", target, body), type="skill", edit="new",
        target=target, body=body, provenance={"source": "demo@s1", "captured": "2026-08-11"},
    )
    staging.write_candidate(home, cand)
    return cand


def stage_fact(home, target="acme-knowledge"):
    body = compose.render_fact_bullet(
        "constraint", "Staging DB resets nightly at 04:00 UTC", ["staging"],
        verified="2026-08-11",
    )
    cand = Candidate(
        id=candidate_id("fact", target, body), type="fact", edit="new",
        target=target, body=body, topic="staging-env",
        provenance={"source": "demo@s1", "captured": "2026-08-11"},
    )
    staging.write_candidate(home, cand)
    return cand


def test_apply_batch_with_remote_pushes_the_harvest_branch(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    gitops.git(target, "remote", "add", "origin", str(remote))
    gitops.git(target, "push", "-u", "origin", "main")
    main_before = gitops.git(target, "rev-parse", "main")
    skill = stage_skill(home)
    fact = stage_fact(home)

    result = harvest.apply_batch(home, "acme-knowledge", [skill, fact])
    assert result.branch.startswith("mneme/harvest-")
    assert gitops.git(target, "rev-parse", "main") == main_before  # main never advances
    assert len(result.units) == 2
    assert result.pr.startswith("manual:")  # no real gh in test PATH... shim not installed
    # branch pushed to the bare remote
    remote_branches = subprocess.run(
        ["git", "-C", str(remote), "branch"], capture_output=True, text=True, check=True
    ).stdout
    assert result.branch in remote_branches
    # repo back on main, clean; branch carries the harvest commit
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    log = gitops.git(target, "log", result.branch, "-1", "--format=%B")
    assert "knowledge: harvest" in log
    assert "Mneme-Source: demo@s1" in log
    # staging emptied, ledger written
    assert staging.load_candidates(home) == []
    ledger = [json.loads(l) for l in paths.submitted_path(home).read_text(encoding="utf-8").splitlines()]
    assert ledger[0]["target"] == "acme-knowledge"
    assert len(ledger[0]["units"]) == 2


def test_apply_batch_no_remote_leaves_the_branch_local(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "personal-kb", owner="demo")
    main_before = gitops.git(target, "rev-parse", "main")
    skill = stage_skill(home, target="personal-kb")
    result = harvest.apply_batch(home, "personal-kb", [skill])
    assert result.branch.startswith("mneme/harvest-")
    assert "no remote" in result.pr
    # The knowledge is on the branch and nowhere else: main is byte-identical and the
    # working tree — back on main — does not carry the new skill.
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert not (target / "skills" / "deploy-widget" / "SKILL.md").exists()
    log = gitops.git(target, "log", result.branch, "-1", "--format=%s")
    assert log.startswith("knowledge: harvest")
    tree = gitops.git(target, "ls-tree", "-r", "--name-only", result.branch)
    assert "skills/deploy-widget/SKILL.md" in tree


def test_apply_batch_refuses_quarantined(tmp_path):
    home = tmp_path / "home"
    scaffold.create(home, "acme-knowledge", owner="demo")
    cand = stage_skill(home)
    staging.quarantine(home, cand.id)
    quarantined = staging.load_candidates(home, include_quarantined=True)[0]
    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "acme-knowledge", [quarantined])


def test_apply_batch_dirty_repo_refused(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    (target / "junk.txt").write_text("dirty", encoding="utf-8")
    cand = stage_skill(home)
    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "acme-knowledge", [cand])


def test_apply_batch_failure_restores_repo_and_keeps_staging(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    good = stage_skill(home)
    # second candidate collides with the first (same skill, edit=new) -> apply fails mid-batch
    dup_body = good.body.replace("what failed", "what failed differently")
    dup = Candidate(
        id=candidate_id("skill", "acme-knowledge", dup_body), type="skill", edit="new",
        target="acme-knowledge", body=dup_body,
        provenance={"source": "demo@s1", "captured": "2026-08-11"},
    )
    staging.write_candidate(home, dup)
    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "acme-knowledge", [good, dup])
    assert gitops.is_clean(target)
    assert gitops.current_branch(target) == "main"
    assert not (target / "skills" / "deploy-widget").exists()
    assert len(staging.load_candidates(home)) == 2  # nothing lost


def test_apply_batch_unknown_target(tmp_path):
    home = tmp_path / "home"
    cand = stage_skill(home, target="ghost-kb")
    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "ghost-kb", [cand])
