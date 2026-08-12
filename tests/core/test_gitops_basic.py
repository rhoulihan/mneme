import subprocess

import pytest

from mneme_core import gitops, paths
from mneme_core.errors import MnemeError


def make_repo(root):
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "add", "-A"], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "commit", "-m", "seed"], check=True, capture_output=True,
    )
    return root


def test_submitted_path(tmp_path):
    assert paths.submitted_path(tmp_path) == tmp_path / "submitted.jsonl"


def test_git_returns_stdout_and_raises_on_failure(tmp_path):
    repo = make_repo(tmp_path / "r")
    assert gitops.git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    with pytest.raises(MnemeError):
        gitops.git(repo, "definitely-not-a-command")


def test_repo_predicates(tmp_path):
    repo = make_repo(tmp_path / "r")
    assert gitops.is_git_repo(repo)
    assert not gitops.is_git_repo(tmp_path / "not-a-repo")
    assert gitops.is_clean(repo)
    (repo / "dirty.txt").write_text("x", encoding="utf-8")
    assert not gitops.is_clean(repo)
    assert gitops.current_branch(repo) == "main"
    assert not gitops.has_remote(repo)


def test_sync_and_branch_without_remote(tmp_path):
    repo = make_repo(tmp_path / "r")
    gitops.create_branch(repo, "mneme/harvest-test")
    assert gitops.current_branch(repo) == "mneme/harvest-test"
    gitops.sync_main(repo)
    assert gitops.current_branch(repo) == "main"


def test_sync_pulls_from_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    repo = make_repo(tmp_path / "r")
    gitops.git(repo, "remote", "add", "origin", str(remote))
    gitops.git(repo, "push", "-u", "origin", "main")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    (other / "new.txt").write_text("new\n", encoding="utf-8")
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(other),
         "add", "-A"], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(other),
         "commit", "-m", "upstream"], check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(other), "push"], check=True, capture_output=True)
    gitops.sync_main(repo)
    assert (repo / "new.txt").exists()


def test_restore_discards_uncommitted(tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "seed.txt").write_text("mutated\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("junk\n", encoding="utf-8")
    gitops.restore(repo)
    assert (repo / "seed.txt").read_text(encoding="utf-8") == "seed\n"
    assert not (repo / "untracked.txt").exists()
