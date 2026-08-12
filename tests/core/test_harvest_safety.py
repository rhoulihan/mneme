"""A harvest either lands or leaves nothing behind.

Every failure path — before the gate, at the gate, and after it (commit, push, PR) —
must restore the knowledge repo to a clean `main` and leave staging intact, or the repo
is wedged forever by the `is_clean` precondition on the next `share apply`.
"""
import subprocess

import pytest

from mneme_core import compose, gitops, harvest, paths, scaffold, staging
from mneme_core.cli import main
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


def bare_remote(tmp_path, target, name="remote.git"):
    remote = tmp_path / name
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    gitops.git(target, "remote", "add", "origin", str(remote))
    gitops.git(target, "push", "-u", "origin", "main")
    return remote


def reject_hook(repo_git_dir, hook):
    """Install a hook that fails, standing in for CI policy / signing / a bad remote."""
    hooks = repo_git_dir / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    path = hooks / hook
    path.write_text("#!/bin/sh\necho 'rejected by policy' >&2\nexit 1\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def assert_pristine(target, base_sha, home, staged=1):
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.head_sha(target) == base_sha
    assert "mneme/harvest-" not in gitops.git(target, "branch")
    assert len(staging.load_candidates(home)) == staged
    assert not paths.submitted_path(home).exists()


def test_malformed_plugin_manifest_aborts_and_restores(tmp_path):
    """A hand-edited manifest raises JSONDecodeError — a non-MnemeError, mid-batch."""
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    (target / ".claude-plugin" / "plugin.json").write_text(
        '{\n  "name": "acme-knowledge",\n  "version": "0.1.0",\n}\n', encoding="utf-8"
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "chore: hand-edit the manifest")
    base = gitops.head_sha(target)
    cand = stage_skill(home)

    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "acme-knowledge", [cand])

    assert not (target / "skills" / "deploy-widget").exists()
    assert_pristine(target, base, home)


def test_malformed_manifest_honours_the_exit_code_contract(tmp_path, capsys):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    (target / ".claude-plugin" / "plugin.json").write_text("{oops}\n", encoding="utf-8")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "chore: hand-edit the manifest")
    cand = stage_skill(home)

    code = main(["--home", str(home), "share", "apply", "--ids", cand.id])
    err = capsys.readouterr().err

    assert code == 1
    assert err.startswith("mneme:")  # not a raw traceback


def test_skill_path_occupied_by_a_file_aborts_and_restores(tmp_path):
    """mkdir raises FileExistsError, not MnemeError — the batch must still restore."""
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    (target / "skills" / "deploy-widget").write_text("not a directory", encoding="utf-8")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "chore: stray file where a skill dir belongs")
    base = gitops.head_sha(target)
    cand = stage_skill(home)

    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "acme-knowledge", [cand])

    assert_pristine(target, base, home)


def test_commit_mode_push_failure_rolls_back_and_retry_succeeds(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "personal-kb", owner="demo", mode="commit")
    remote = bare_remote(tmp_path, target)
    hook = reject_hook(remote, "pre-receive")
    base = gitops.head_sha(target)
    cand = stage_skill(home, target="personal-kb")

    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "personal-kb", [cand])

    # The harvest commit is gone with it: nothing half-landed on main.
    assert_pristine(target, base, home)
    assert not (target / "skills" / "deploy-widget").exists()

    # ...and the identical harvest simply works once the remote accepts pushes again.
    hook.unlink()
    result = harvest.apply_batch(home, "personal-kb", staging.load_candidates(home))
    assert result.pr == "pushed to main"
    assert staging.load_candidates(home) == []
    assert paths.submitted_path(home).exists()
    assert (target / "skills" / "deploy-widget" / "SKILL.md").exists()


def test_rejected_commit_rolls_back(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "personal-kb", owner="demo", mode="commit")
    reject_hook(target / ".git", "pre-commit")
    base = gitops.head_sha(target)
    cand = stage_skill(home, target="personal-kb")

    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "personal-kb", [cand], push=False)

    assert_pristine(target, base, home)
    assert not (target / "skills" / "deploy-widget").exists()


def test_pr_mode_push_failure_returns_to_clean_main(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    remote = bare_remote(tmp_path, target)
    reject_hook(remote, "pre-receive")
    base = gitops.head_sha(target)
    cand = stage_skill(home)

    with pytest.raises(MnemeError):
        harvest.apply_batch(home, "acme-knowledge", [cand])

    # No orphan commit stranded on an abandoned harvest branch.
    assert_pristine(target, base, home)
