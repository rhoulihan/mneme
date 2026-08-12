import os
import stat
import subprocess

from mneme_core import gitops


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


def shim_gh(tmp_path, monkeypatch, script):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(script, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def test_open_pr_uses_gh(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    shim_gh(
        tmp_path, monkeypatch,
        "#!/bin/sh\necho https://github.com/acme/kb/pull/7\n",
    )
    url = gitops.open_pr(repo, "mneme/harvest-x", "knowledge: harvest", "body text")
    assert url == "https://github.com/acme/kb/pull/7"


def test_open_pr_gh_failure_degrades(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    shim_gh(tmp_path, monkeypatch, "#!/bin/sh\necho boom >&2\nexit 1\n")
    result = gitops.open_pr(repo, "mneme/harvest-x", "title", "body")
    assert result.startswith("manual:")
    assert "mneme/harvest-x" in result


def test_open_pr_gh_missing_degrades(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    bindir = tmp_path / "emptybin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    result = gitops.open_pr(repo, "b", "t", "body")
    assert result.startswith("manual:")
