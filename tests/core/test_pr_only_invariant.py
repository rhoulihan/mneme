"""The PR-only invariant: mneme never advances a registered repo's `main`.

Every harvest lands on a `mneme/harvest-*` branch. With a remote the branch is pushed
(and a PR opened); without one it is left local for the human. Either way `main` — local
and remote — is exactly where the harvest found it.
"""
import subprocess

from mneme_core import compose, gitops, harvest, scaffold, staging
from mneme_core.staging import Candidate, candidate_id


def stage_skill(home, target):
    body = compose.render_skill_unit(
        "pr-only-skill", "Use when proving the PR-only invariant", "1. steps",
        "direct commits to main were the failure", source="s@1", captured="2026-08-12",
    )
    cand = Candidate(
        id=candidate_id("skill", target, body), type="skill", edit="new",
        target=target, body=body, provenance={"source": "s@1", "captured": "2026-08-12"},
    )
    staging.write_candidate(home, cand)
    return cand


def test_main_never_advances_without_remote(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "local-kb", owner="demo")
    main_before = gitops.git(target, "rev-parse", "main")
    cand = stage_skill(home, "local-kb")
    result = harvest.apply_batch(home, "local-kb", [cand])
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert result.branch.startswith("mneme/harvest-")
    assert "no remote" in result.pr
    assert gitops.current_branch(target) == "main"
    branch_tip = gitops.git(target, "rev-parse", result.branch)
    assert branch_tip != main_before


def test_remote_main_never_advances(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "remote-kb", owner="demo")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    gitops.git(target, "remote", "add", "origin", str(remote))
    gitops.git(target, "push", "-u", "origin", "main")
    remote_main_before = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    cand = stage_skill(home, "remote-kb")
    result = harvest.apply_batch(home, "remote-kb", [cand])
    remote_main_after = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert remote_main_after == remote_main_before
    remote_branches = subprocess.run(
        ["git", "-C", str(remote), "branch"], capture_output=True, text=True, check=True
    ).stdout
    assert result.branch in remote_branches


def test_push_main_is_gone():
    assert not hasattr(gitops, "push_main")
