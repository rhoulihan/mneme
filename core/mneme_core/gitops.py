"""Git side effects for harvest — subprocess-wrapped, never networked implicitly (spec §7.3, §8)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import units
from dataclasses import dataclass

from .errors import MnemeError

# Fully qualified so no local branch or tag spelled `origin/main` can answer for the
# remote-tracking ref this comparison is about.
_ORIGIN_MAIN = "refs/remotes/origin/main"

# How many open pull requests one triage reads. Everything past it is unseen work, which
# is why `list_open_prs` reports a full page rather than pretending it was the whole queue.
FORK_REMOTE = "mneme-fork"
PR_LIST_LIMIT = 100


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


def git_bytes(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """Raw BYTES from git, with optional stdin — for content that is not text.

    `git_raw` decodes as text, which is right for paths and refs and wrong for blobs: a PNG
    or a UTF-16 file raises UnicodeDecodeError deep inside a caller that is only trying to
    read repo content, and an exception of a type no gate expects escapes into a rollback.
    A scanner in particular must take the bytes and decide about encoding itself.
    """
    cmd = [
        "git",
        "-c", "user.name=mneme",
        "-c", "user.email=mneme@localhost",
        "-C", str(repo),
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, input=stdin)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[:300]
        raise MnemeError(f"git {' '.join(args)} failed: {detail}")
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


def behind_origin_main(repo: Path) -> bool | None:
    """Does `origin/main` carry commits this clone's HEAD does not — None if unknowable.

    A local clone is a snapshot: every question mneme answers from it ("is this fact
    already here?", "is this PR a duplicate?") is answered against whatever `main` this
    machine last fetched. None is not "up to date" — it is "there is no remote ref to
    compare against", which a reader must be told apart from a confirmed match.
    """
    try:
        git(repo, "rev-parse", "--verify", "--quiet", _ORIGIN_MAIN)
    except MnemeError:
        return None
    return int(git(repo, "rev-list", "--count", f"HEAD..{_ORIGIN_MAIN}")) > 0


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


def _one_line(value: str) -> str:
    """Collapse anything a message treats as structure into a space.

    A source reaches here off the CANDIDATE FILE, not off `--source`: frontmatter escapes
    a newline and `units._unescape` faithfully restores it, so every candidate staged
    before the flag was validated still arrives with one. Collapsing rather than refusing
    keeps a harvest of pre-existing candidates working -- provenance is metadata, and
    losing a line break in it costs nothing next to blocking the knowledge.
    """
    out = value
    for ch in units.LINE_BREAKS:
        out = out.replace(ch, " ")
    return out.strip()


def harvest_message(
    unit_lines: list[str], sources: list[str], migrated: list[str] | None = None
) -> str:
    """Build the harvest commit message. Split out from the commit so the trailer rules
    are testable without a git repository."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"knowledge: harvest {date} ({len(unit_lines)} units)"
    sections = ["\n".join(f"- {line}" for line in unit_lines)]
    if migrated:
        sections.append("Migrated:\n" + "\n".join(f"- {line}" for line in migrated))
    trailers = sorted({_one_line(s) for s in sources})
    sections.append("\n".join(f"Mneme-Source: {s}" for s in trailers))
    return subject + "\n\n" + "\n\n".join(sections) + "\n"


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
    message = harvest_message(unit_lines, sources, migrated)
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


def list_open_prs(repo: Path, limit: int = PR_LIST_LIMIT) -> tuple[list[dict], bool]:
    """`(open pull requests, truncated)` — author flattened to its login string.

    `gh` answers a capped listing, so a repo with more open pull requests than the cap
    silently loses the rest: triage read them as "all of them" and a maintainer could
    close the queue on an incomplete picture. A full page is not proof more exist, but it
    is exactly the case where they might — so the caller is told, and says so.
    """
    out = _gh_read(
        repo, "pr", "list", "--state", "open",
        "--json", "number,title,headRefName,author,url", "--limit", str(int(limit)),
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
    return prs, len(prs) >= limit


def pr_diff(repo: Path, number: int) -> str:
    """The unified diff of pull request `number` — UNTRUSTED contributor text, read-only."""
    return _gh_read(repo, "pr", "diff", str(int(number)))


@dataclass
class PullRequest:
    """Whether a pull request was opened, and the reason when it was not.

    This used to be a bare string: the URL on success, and a prose sentence on every
    failure — `gh` missing, `gh pr create` erroring. A caller could not tell the two
    apart, so the last step of the human gate could report "manual: branch is pushed"
    and read, to anything downstream, exactly like a delivered contribution.
    """

    url: str = ""
    reason: str = ""

    @property
    def opened(self) -> bool:
        return bool(self.url)

    def describe(self) -> str:
        """What a caller prints — the URL, or why there isn't one."""
        return self.url or self.reason


def _gh_pr_create(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", "pr", "create", *args], capture_output=True, text=True, cwd=str(repo)
    )


def open_pr(
    repo: Path, branch: str, title: str, body: str, *, required: bool = False
) -> PullRequest:
    """Open a pull request for `branch`, or say why not.

    `required=True` is for a contribution into a repo that is not the user's own: there is
    no local fallback there, and leaving the branch behind with a cheerful sentence is how
    a contribution silently never arrives. For the user's own repo the fallback is right —
    no `gh`, branch pushed, open it yourself — so it stays the default.
    """
    if shutil.which("gh") is None:
        # The `manual:` prefix is load-bearing: it is the marker callers and users have
        # always matched on to tell a pushed-but-unopened branch from a URL.
        reason = (
            f"manual: gh (GitHub CLI) is not installed — branch '{branch}' is pushed;"
            f" open the pull request yourself (title: {title})"
        )
        if required:
            raise MnemeError(reason)
        return PullRequest(reason=reason)
    result = _gh_pr_create(
        repo, "--head", branch, "--title", title, "--body", body
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:300]
        reason = (
            f"manual: gh pr create failed ({detail}) — branch '{branch}' is pushed;"
            f" open the pull request yourself (title: {title})"
        )
        if required:
            raise MnemeError(reason)
        return PullRequest(reason=reason)
    return PullRequest(url=result.stdout.strip())


def fork_and_pr(repo: Path, branch: str, title: str, body: str) -> PullRequest:
    """Contribute to a repo the user cannot push to.

    `push_branch` is `git push -u origin`, which is a 403 for anyone who is not a
    collaborator — and it fails AFTER the human gate has passed, which is the worst place
    available. A contributor to somebody else's knowledge repo is a contributor by
    definition, so this is the path that case takes: fork, push there, and open the pull
    request against the UPSTREAM repo with a user-qualified head.

    `gh` is a hard requirement here. There is no manual fallback worth offering: the
    branch would be sitting in a fork the user has to find.
    """
    upstream = _repo_name_with_owner(repo)
    # Idempotent by intent, not by exit code: `gh repo fork` returns non-zero once the
    # fork exists, which is the normal case on every contribution after the first. Both
    # streams are read because "already exists" goes to stderr.
    forked = subprocess.run(
        ["gh", "repo", "fork", "--remote", "--remote-name", FORK_REMOTE],
        capture_output=True, text=True, cwd=str(repo),
    )
    owner = _fork_owner(f"{forked.stdout}\n{forked.stderr}", repo)
    if _has_named_remote(repo, FORK_REMOTE):
        git(repo, "push", "-u", FORK_REMOTE, branch)
    result = _gh_pr_create(
        repo, "--repo", upstream, "--head", f"{owner}:{branch}",
        "--title", title, "--body", body,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:300]
        raise MnemeError(
            f"could not open a pull request against {upstream} from {owner}:{branch}"
            f" — {detail}"
        )
    return PullRequest(url=result.stdout.strip())


def _repo_name_with_owner(repo: Path) -> str:
    out = _gh_read(repo, "repo", "view", "--json", "nameWithOwner")
    try:
        name = json.loads(out or "{}").get("nameWithOwner", "")
    except json.JSONDecodeError as exc:
        raise MnemeError(f"gh repo view returned unreadable JSON: {exc}") from exc
    if not name:
        raise MnemeError("gh repo view did not name an upstream repository")
    return str(name)


_FORK_NAME_RE = re.compile(r"\b([A-Za-z0-9][\w.-]*)/[\w.-]+\b")


def _fork_owner(fork_output: str, repo: Path) -> str:
    """The account the fork lives under.

    Taken from `gh repo fork`'s own output, which names it on both paths — "Created fork
    you/kb" and "you/kb already exists". That is the authoritative answer, because it is
    the account the branch will actually be pushed to; `gh api user` is the fallback for
    an output format that does not name it.
    """
    for line in fork_output.splitlines():
        match = _FORK_NAME_RE.search(line)
        if match:
            return match.group(1)
    login = _gh_read(repo, "api", "user", "--jq", ".login").strip()
    if login:
        return login
    raise MnemeError(
        "could not determine the forking account, so --head <owner>:<branch> cannot be"
        " built — without the owner the pull request opens against the fork instead of"
        " upstream"
    )


def _has_named_remote(repo: Path, name: str) -> bool:
    return name in git(repo, "remote").splitlines()
