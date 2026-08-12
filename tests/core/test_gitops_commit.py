import subprocess

import pytest

from mneme_core import gitops
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


def test_commit_harvest_message_shape(tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "skills").mkdir()
    (repo / "skills" / "x.md").write_text("unit\n", encoding="utf-8")
    sha = gitops.commit_harvest(
        repo,
        ["skills/deploy-widget (new skill)", "facts/staging-env#db-resets (new fact)"],
        ["demo@s1", "demo@s1"],
    )
    assert len(sha) == 40
    message = gitops.git(repo, "log", "-1", "--format=%B")
    assert message.splitlines()[0].startswith("knowledge: harvest ")
    assert "(2 units)" in message.splitlines()[0]
    assert "- skills/deploy-widget (new skill)" in message
    assert "Mneme-Source: demo@s1" in message
    assert message.count("Mneme-Source:") == 1  # deduplicated


def test_commit_harvest_nothing_to_commit(tmp_path):
    repo = make_repo(tmp_path / "r")
    with pytest.raises(MnemeError):
        gitops.commit_harvest(repo, ["x"], ["s"])


def test_push_branch_to_bare_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    repo = make_repo(tmp_path / "r")
    gitops.git(repo, "remote", "add", "origin", str(remote))
    gitops.git(repo, "push", "-u", "origin", "main")
    gitops.create_branch(repo, "mneme/harvest-x")
    (repo / "new.txt").write_text("n\n", encoding="utf-8")
    gitops.commit_harvest(repo, ["facts/t#k (new fact)"], ["s1"])
    gitops.push_branch(repo, "mneme/harvest-x")
    remote_branches = subprocess.run(
        ["git", "-C", str(remote), "branch"], capture_output=True, text=True, check=True
    ).stdout
    assert "mneme/harvest-x" in remote_branches


def test_push_without_remote_raises(tmp_path):
    repo = make_repo(tmp_path / "r")
    with pytest.raises(MnemeError):
        gitops.push_branch(repo, "main")
