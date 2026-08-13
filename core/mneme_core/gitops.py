"""Git side effects for harvest — subprocess-wrapped, never networked implicitly (spec §7.3, §8)."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .errors import MnemeError


def git_raw(repo: Path, *args: str) -> str:
    """Exactly what git wrote to stdout — nothing trimmed.

    `git` below strips, which is right for the single values most callers want and wrong
    for machine formats: a `status --porcelain -z` record begins with its status field,
    whose first character is a space for an unstaged change, and stripping that away
    shifts the record's path by one byte.
    """
    cmd = [
        "git",
        "-c", "user.name=mneme",
        "-c", "user.email=mneme@localhost",
        "-C", str(repo),
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise MnemeError(f"git {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def git(repo: Path, *args: str) -> str:
    return git_raw(repo, *args).strip()


def is_git_repo(repo: Path) -> bool:
    return (repo / ".git").exists()


def is_clean(repo: Path) -> bool:
    return git(repo, "status", "--porcelain") == ""


def current_branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def has_remote(repo: Path) -> bool:
    return "origin" in git(repo, "remote").splitlines()


def sync_main(repo: Path) -> None:
    git(repo, "checkout", "main")
    if has_remote(repo):
        git(repo, "pull", "--ff-only", "origin", "main")


def create_branch(repo: Path, name: str) -> None:
    git(repo, "checkout", "-b", name)


def head_sha(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def restore(repo: Path) -> None:
    git(repo, "checkout", "--", ".")
    git(repo, "clean", "-fd")


def reset_hard(repo: Path, sha: str) -> None:
    """Drop every commit and working-tree change made after `sha` on the current branch."""
    git(repo, "reset", "--hard", sha)


def commit_harvest(
    repo: Path,
    unit_lines: list[str],
    sources: list[str],
    migrated: list[str] | None = None,
) -> str:
    """Commit the harvest; record a layout migration that rode along in its own section.

    `migrated` is deliberately NOT folded into `unit_lines`: the unit lines are what this
    harvest contributed — the ledger, the pull request title's count, `mneme share view` —
    while a migration note describes knowledge the repo already had, moved. A reviewer
    reads the second list to check that a large diff moved facts rather than rewriting
    them, and would have to guess at it if the two were one list.
    """
    git(repo, "add", "-A")
    if git(repo, "status", "--porcelain") == "":
        raise MnemeError("nothing to commit for this harvest")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"knowledge: harvest {date} ({len(unit_lines)} units)"
    sections = ["\n".join(f"- {line}" for line in unit_lines)]
    if migrated:
        sections.append("Migrated:\n" + "\n".join(f"- {line}" for line in migrated))
    sections.append("\n".join(f"Mneme-Source: {s}" for s in sorted(set(sources))))
    message = subject + "\n\n" + "\n\n".join(sections) + "\n"
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def push_branch(repo: Path, branch: str) -> None:
    if not has_remote(repo):
        raise MnemeError("no 'origin' remote to push to")
    git(repo, "push", "-u", "origin", branch)


def _gh_read(repo: Path, *args: str) -> str:
    """Run a READ-ONLY `gh` command in `repo`; `gh` is a hard requirement here.

    Unlike `open_pr`, which degrades to a manual instruction when `gh` is missing, review
    triage has nothing to fall back on — there is no other way to see the open PRs — so an
    absent or failing `gh` is an error that names the requirement.
    """
    if shutil.which("gh") is None:
        raise MnemeError(
            f"gh (GitHub CLI) is required for 'gh {' '.join(args)}' — install it from"
            " https://cli.github.com and run 'gh auth login'"
        )
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, cwd=str(repo),
    )
    if result.returncode != 0:
        raise MnemeError(f"gh {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def list_open_prs(repo: Path) -> list[dict]:
    """Open pull requests for `repo`, newest API shape flattened: author is its login string."""
    out = _gh_read(
        repo, "pr", "list", "--state", "open",
        "--json", "number,title,headRefName,author,url", "--limit", "50",
    )
    try:
        prs = json.loads(out or "[]")
    except json.JSONDecodeError as exc:
        raise MnemeError(f"gh pr list returned unreadable JSON: {exc}") from exc
    if not isinstance(prs, list):
        raise MnemeError("gh pr list returned unreadable JSON: expected a list")
    for pr in prs:
        author = pr.get("author")
        pr["author"] = author.get("login", "") if isinstance(author, dict) else (author or "")
    return prs


def pr_diff(repo: Path, number: int) -> str:
    """The unified diff of pull request `number` — UNTRUSTED contributor text, read-only."""
    return _gh_read(repo, "pr", "diff", str(int(number)))


def open_pr(repo: Path, branch: str, title: str, body: str) -> str:
    fallback = (
        f"manual: branch '{branch}' is pushed — open the pull request yourself"
        f" (title: {title})"
    )
    if shutil.which("gh") is None:
        return fallback
    result = subprocess.run(
        ["gh", "pr", "create", "--head", branch, "--title", title, "--body", body],
        capture_output=True, text=True, cwd=str(repo),
    )
    if result.returncode != 0:
        return fallback
    return result.stdout.strip()
