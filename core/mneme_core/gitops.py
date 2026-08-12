"""Git side effects for harvest — subprocess-wrapped, never networked implicitly (spec §7.3, §8)."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .errors import MnemeError


def git(repo: Path, *args: str) -> str:
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
    return result.stdout.strip()


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


def restore(repo: Path) -> None:
    git(repo, "checkout", "--", ".")
    git(repo, "clean", "-fd")


def commit_harvest(repo: Path, unit_lines: list[str], sources: list[str]) -> str:
    git(repo, "add", "-A")
    if git(repo, "status", "--porcelain") == "":
        raise MnemeError("nothing to commit for this harvest")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"knowledge: harvest {date} ({len(unit_lines)} units)"
    body_lines = [f"- {line}" for line in unit_lines]
    trailers = [f"Mneme-Source: {s}" for s in sorted(set(sources))]
    message = subject + "\n\n" + "\n".join(body_lines) + "\n\n" + "\n".join(trailers) + "\n"
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def push_branch(repo: Path, branch: str) -> None:
    if not has_remote(repo):
        raise MnemeError("no 'origin' remote to push to")
    git(repo, "push", "-u", "origin", branch)


def push_main(repo: Path) -> None:
    if not has_remote(repo):
        raise MnemeError("no 'origin' remote to push to")
    git(repo, "push", "origin", "main")
