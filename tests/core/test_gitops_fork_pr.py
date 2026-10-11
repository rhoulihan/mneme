"""Opening a pull request you can actually check, and opening one from a fork.

Two defects the Funes spec named. `open_pr` returned a PROSE STRING on every failure —
`gh` missing, `gh pr create` erroring — so a caller could not tell "pull request opened"
from "pull request not opened", and a gate whose last step cannot distinguish those is not
a gate. And there was no fork path at all: `push_branch` is `git push -u origin`, which is
a 403 for a contributor who is not a collaborator — after the human gate has already
passed, which is the worst possible place to fail.

The prose fallback itself is kept. It is right for the local workflow: no `gh`, branch
pushed, open it yourself. What changes is that the result says which happened.
"""
import os
import stat
import subprocess

import pytest

from mneme_core import gitops
from mneme_core.errors import MnemeError


def make_repo(root, *, remote=True):
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    for args in (["add", "-A"], ["commit", "-m", "seed"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@local",
                        "-C", str(root), *args], check=True, capture_output=True)
    if remote:
        bare = root.parent / (root.name + "-origin.git")
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(bare)],
                       check=True, capture_output=True)
    return root


def shim_gh(tmp_path, monkeypatch, script):
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text(script, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def hide_gh(tmp_path, monkeypatch):
    empty = tmp_path / "nobin"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))


# --- a result you can check -------------------------------------------------


def test_a_successful_pr_reports_its_url(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    shim_gh(tmp_path, monkeypatch, "#!/bin/sh\necho https://github.com/acme/kb/pull/7\n")
    pr = gitops.open_pr(repo, "mneme/harvest-x", "knowledge: harvest", "body")
    assert pr.opened is True
    assert pr.url == "https://github.com/acme/kb/pull/7"
    assert pr.reason == ""


def test_a_missing_gh_is_reported_as_not_opened(tmp_path, monkeypatch):
    """The fallback stays — but `opened` is False, which is the part that was missing."""
    repo = make_repo(tmp_path / "r")
    hide_gh(tmp_path, monkeypatch)
    pr = gitops.open_pr(repo, "mneme/harvest-x", "title", "body")
    assert pr.opened is False
    assert pr.url == ""
    assert "gh" in pr.reason
    assert "mneme/harvest-x" in pr.describe()


def test_a_failing_gh_is_reported_as_not_opened(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    shim_gh(tmp_path, monkeypatch, "#!/bin/sh\necho 'boom' >&2\nexit 1\n")
    pr = gitops.open_pr(repo, "mneme/harvest-x", "title", "body")
    assert pr.opened is False
    assert "boom" in pr.reason


@pytest.mark.parametrize("script", [None, "#!/bin/sh\necho boom >&2\nexit 1\n"])
def test_required_turns_a_non_opened_pr_into_an_error(tmp_path, monkeypatch, script):
    """For a contribution into somebody else's repo there is no local fallback — leaving
    the branch behind with a cheerful sentence is how a contribution silently never
    arrives."""
    repo = make_repo(tmp_path / "r")
    if script is None:
        hide_gh(tmp_path, monkeypatch)
    else:
        shim_gh(tmp_path, monkeypatch, script)
    with pytest.raises(MnemeError):
        gitops.open_pr(repo, "mneme/harvest-x", "title", "body", required=True)


def test_describe_is_what_a_caller_prints(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    shim_gh(tmp_path, monkeypatch, "#!/bin/sh\necho https://github.com/acme/kb/pull/7\n")
    assert gitops.open_pr(repo, "b", "t", "x").describe() == (
        "https://github.com/acme/kb/pull/7"
    )


# --- the fork path ---------------------------------------------------------


FORK_GH = """#!/bin/sh
case "$1 $2" in
  "repo view")  echo '{"nameWithOwner":"acme/team-kb"}' ;;
  "repo fork")  echo 'Created fork you/team-kb' ;;
  "pr create")  echo 'https://github.com/acme/team-kb/pull/42' ;;
  *) echo "unexpected: $*" >&2; exit 1 ;;
esac
"""


def test_a_fork_pr_pushes_to_the_fork_and_targets_upstream(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    calls = tmp_path / "calls.log"
    shim_gh(tmp_path, monkeypatch, FORK_GH.replace(
        "case \"$1 $2\" in", f'echo "$*" >> {calls}\ncase "$1 $2" in'))
    pr = gitops.fork_and_pr(repo, "mneme/harvest-x", "knowledge: harvest", "body")
    assert pr.opened and pr.url.endswith("/pull/42")
    log = calls.read_text(encoding="utf-8")
    assert "repo fork" in log
    # The PR must target the UPSTREAM repo with a user-qualified head, or it opens against
    # the fork and the maintainer never sees it.
    assert "--repo acme/team-kb" in log
    assert "--head you:mneme/harvest-x" in log


def test_a_fork_pr_requires_gh(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    hide_gh(tmp_path, monkeypatch)
    with pytest.raises(MnemeError) as e:
        gitops.fork_and_pr(repo, "b", "t", "x")
    assert "gh" in str(e.value)


def test_an_existing_fork_is_not_an_error(tmp_path, monkeypatch):
    """`gh repo fork` on a repo you have already forked is the normal case on the second
    contribution, and it must not read as a failure."""
    repo = make_repo(tmp_path / "r")
    shim_gh(tmp_path, monkeypatch, FORK_GH.replace(
        "echo 'Created fork you/team-kb'",
        "echo 'you/team-kb already exists' >&2\nexit 1"))
    pr = gitops.fork_and_pr(repo, "mneme/harvest-x", "t", "x")
    assert pr.opened, "an already-existing fork aborted the contribution"


def test_a_failed_fork_pr_is_an_error_not_a_sentence(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    shim_gh(tmp_path, monkeypatch, FORK_GH.replace(
        "echo 'https://github.com/acme/team-kb/pull/42'", "echo denied >&2\nexit 1"))
    with pytest.raises(MnemeError):
        gitops.fork_and_pr(repo, "mneme/harvest-x", "t", "x")
