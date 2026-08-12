"""Classify rails — branch discipline around the prompt-driven librarian pass (spec §7.7).

Classification itself is LLM judgment over repo structures that vary, so it lives in the
session. These rails are the deterministic frame around it: the directory the user is
standing in must resolve to a registered knowledge plugin, the work happens on a
`mneme/classify-*` branch, and `main` is never written (Plan 09 doctrine).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import gitops
from .errors import MnemeError

BRANCH_PREFIX = "mneme/classify-"


def resolve(home: Path, cwd: Path):
    """The registered plugin containing `cwd`, plus its repo root.

    The directory IS the argument — classify never takes a plugin name — so this is the
    one place that turns "where the user is" into "which repo may be rewritten", and the
    failure message has to tell them exactly how to get a directory that qualifies.
    """
    from . import routing

    scope = routing.plugin_for_path(home, cwd)
    if scope is None:
        raise MnemeError(
            "this directory is not inside a registered knowledge plugin —"
            " cd into one or register it first (/mneme:register)"
        )
    repo = Path(scope.path)
    if not gitops.is_git_repo(repo):
        raise MnemeError(f"{repo} is not a git repository")
    return scope, repo


def begin(home: Path, cwd: Path) -> str:
    _scope, repo = resolve(home, cwd)
    # Order matters: an already-active classify branch is the more specific diagnosis, and
    # such a branch is usually dirty by design (the agent is mid-edit) — reporting it as
    # "uncommitted changes" would send the user to stash work the abort rail exists for.
    if gitops.current_branch(repo).startswith(BRANCH_PREFIX):
        raise MnemeError("a classify branch is already active — finalize or abort it first")
    if not gitops.is_clean(repo):
        raise MnemeError(f"{repo} has uncommitted changes — commit or stash them first")
    gitops.sync_main(repo)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"{BRANCH_PREFIX}{stamp}"
    gitops.create_branch(repo, branch)
    return branch


def abort(home: Path, cwd: Path) -> None:
    _scope, repo = resolve(home, cwd)
    branch = gitops.current_branch(repo)
    if not branch.startswith(BRANCH_PREFIX):
        raise MnemeError("not on a classify branch — nothing to abort")
    gitops.restore(repo)
    gitops.git(repo, "checkout", "main")
    gitops.git(repo, "branch", "-D", branch)
