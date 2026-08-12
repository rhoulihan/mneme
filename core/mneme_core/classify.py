"""Classify rails — branch discipline around the prompt-driven librarian pass (spec §7.7).

Classification itself is LLM judgment over repo structures that vary, so it lives in the
session. These rails are the deterministic frame around it: the directory the user is
standing in must resolve to a registered knowledge plugin, the work happens on a
`mneme/classify-*` branch, and `main` is never written (Plan 09 doctrine).

Review extraction (spec §7.8) needs the identical frame — an agent editing the same
working tree under the same gates — differing only in the branch namespace, the commit
subject, and the ledger kind. So the rails take a `kind` ("classify" or "review") and both
flows run the SAME code: a gate the review rail skipped would be a gate that stopped
holding for knowledge arriving from strangers, which is the traffic that needs it most.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import gitops, harvest, lint, paths, scan, templates, units
from .errors import MnemeError

# The kind word is the whole difference between the rails: it names the branch namespace,
# the commit subject, the ledger record, and every message the user reads.
_RAIL_KINDS = ("classify", "review")


def _branch_prefix(kind: str) -> str:
    return f"mneme/{kind}-"


BRANCH_PREFIX = _branch_prefix("classify")
REVIEW_BRANCH_PREFIX = _branch_prefix("review")


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


def _active_rail(repo: Path) -> str | None:
    """The kind of rail branch this repo is standing on, or None."""
    branch = gitops.current_branch(repo)
    for kind in _RAIL_KINDS:
        if branch.startswith(_branch_prefix(kind)):
            return kind
    return None


def _begin(home: Path, cwd: Path, kind: str) -> str:
    _scope, repo = resolve(home, cwd)
    # Order matters: an already-active rail branch is the more specific diagnosis, and
    # such a branch is usually dirty by design (the agent is mid-edit) — reporting it as
    # "uncommitted changes" would send the user to stash work the abort rail exists for.
    # Either kind blocks either begin: both flows rewrite the one working tree, so a
    # review extraction started mid-classify would deliver the librarian's edits too.
    active = _active_rail(repo)
    if active is not None:
        raise MnemeError(f"a {active} branch is already active — finalize or abort it first")
    if not gitops.is_clean(repo):
        raise MnemeError(f"{repo} has uncommitted changes — commit or stash them first")
    gitops.sync_main(repo)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"{_branch_prefix(kind)}{stamp}"
    gitops.create_branch(repo, branch)
    return branch


def _abort(home: Path, cwd: Path, kind: str) -> None:
    _scope, repo = resolve(home, cwd)
    branch = gitops.current_branch(repo)
    # Deliberately the one prefix, not both: `mneme review abort` deleting a classify
    # branch would discard a pass its user never asked to end.
    if not branch.startswith(_branch_prefix(kind)):
        raise MnemeError(f"not on a {kind} branch — nothing to abort")
    gitops.restore(repo)
    gitops.git(repo, "checkout", "main")
    gitops.git(repo, "branch", "-D", branch)


def begin(home: Path, cwd: Path) -> str:
    return _begin(home, cwd, "classify")


def abort(home: Path, cwd: Path) -> None:
    _abort(home, cwd, "classify")


def review_begin(home: Path, cwd: Path) -> str:
    return _begin(home, cwd, "review")


def review_abort(home: Path, cwd: Path) -> None:
    _abort(home, cwd, "review")


def _fact_entries(repo: Path, notes: list[str]) -> list[dict]:
    # `units.fact_files` sweeps both layouts (canonical first): the librarian has to *see*
    # every fact, and a repo mid-migration can carry both.
    entries: list[dict] = []
    for f in units.fact_files(repo):
        rel = f.relative_to(repo).as_posix()
        try:
            text = f.read_text(encoding="utf-8-sig")
            meta, body = units.parse_frontmatter(text)
        except (MnemeError, OSError, UnicodeDecodeError) as e:
            notes.append(f"{rel}: unreadable ({e})")
            continue
        topic = str(meta.get("topic", f.stem))
        # Absolute line numbers, so the librarian can point at the bullet it moved.
        offset = len(text.splitlines()) - len(body.splitlines())
        for n, line in enumerate(body.splitlines(), start=1):
            if not line.startswith("- ["):
                continue
            try:
                bullet = units.parse_bullet_line(line, n)
            except MnemeError:
                notes.append(f"{rel}:{offset + n}: malformed fact bullet — left in place")
                continue
            entries.append(
                {
                    "file": rel,
                    "topic": topic,
                    "line": offset + n,
                    "category": bullet.category,
                    "text": bullet.text,
                    "tags": bullet.tags,
                    "verified": bullet.verified or "",
                    # Physical location never enters the id: a fact keeps its identity
                    # (and its declined-ledger / similar-to continuity) across the move.
                    "unit_id": units.fact_unit_id(f.stem, bullet.text),
                }
            )
    return entries


def _skill_entries(repo: Path, notes: list[str]) -> list[dict]:
    entries: list[dict] = []
    skills_dir = repo / "skills"
    if not skills_dir.is_dir():
        return entries
    for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        # knowledge-index is generated from the fact files — never a destination.
        if d.name == "knowledge-index":
            continue
        skill_md = d / "SKILL.md"
        rel_dir = d.relative_to(repo).as_posix()
        if not skill_md.is_file():
            notes.append(f"{rel_dir}: no SKILL.md — not a destination")
            continue
        try:
            meta, _body = units.parse_frontmatter(skill_md.read_text(encoding="utf-8-sig"))
        except (MnemeError, OSError, UnicodeDecodeError) as e:
            notes.append(f"{rel_dir}/SKILL.md: unreadable ({e})")
            continue
        entries.append(
            {
                "name": str(meta.get("name", d.name)),
                "description": str(meta.get("description", "")),
                "dir": rel_dir,
                "files": sorted(
                    p.relative_to(repo).as_posix() for p in d.rglob("*") if p.is_file()
                ),
            }
        )
    return entries


def bundle(home: Path, cwd: Path) -> dict:
    """Everything the in-session librarian needs, and nothing it has to guess."""
    scope, repo = resolve(home, cwd)
    notes: list[str] = []
    return {
        "plugin": scope.name,
        "repo": str(repo),
        "facts": _fact_entries(repo, notes),
        "skills": _skill_entries(repo, notes),
        "legacy_layout": (repo / "facts").is_dir(),
        "notes": notes,
        "instructions": templates.CLASSIFY_INSTRUCTIONS,
    }


def _migrate_legacy_facts(repo: Path) -> list[tuple[str, str]]:
    """Move whatever still lives in a top-level `facts/` under the router skill.

    Uses `git mv` for tracked files so the history of a fact follows it across the move.
    Destinations are built from the walk itself (never from candidate-supplied text), and
    the containment proof below keeps that true even if a hostile name ever appears.
    """
    legacy = repo / "facts"
    if not legacy.is_dir():
        return []
    canonical = repo / units.FACTS_CANONICAL
    moves: list[tuple[str, str]] = []
    for src in sorted(p for p in legacy.rglob("*") if p.is_file()):
        rel = src.relative_to(legacy).as_posix()
        dest = canonical / rel
        rel_src = f"facts/{rel}"
        rel_dest = f"{units.FACTS_CANONICAL}/{rel}"
        if not dest.resolve().is_relative_to(canonical.resolve()):
            raise MnemeError(f"legacy fact escapes the facts directory: {rel_src}")
        if dest.exists():
            if src.name == ".gitkeep":
                # Both layouts carrying a placeholder is not a conflict — drop the old one.
                gitops.git(repo, "rm", "-f", "--ignore-unmatch", "--quiet", "--", rel_src)
                if src.exists():
                    src.unlink()
                continue
            raise MnemeError(
                f"both fact layouts carry {rel} — merge {rel_src} into {rel_dest} by hand,"
                " then run classify again"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        if gitops.git(repo, "ls-files", "--", rel_src):
            gitops.git(repo, "mv", "--", rel_src, rel_dest)
        else:
            # A file the librarian just created: nothing for git to rename yet.
            src.rename(dest)
        moves.append((rel_src, rel_dest))
    return moves


def _changed_files(repo: Path) -> list[str]:
    """Every path this classify pass touches — committed on the branch or still working.

    Both queries run in `-z` form and read through `git_raw`: NUL-separated paths are
    never quoted or line-split, so a filename containing a space, a quote, or a newline
    reaches the secret-scan gate intact instead of being silently skipped.

    `--untracked-files=all` is load-bearing, not tidiness: by default git collapses a
    wholly-untracked directory into a single `dir/` record, and a *directory* is not a
    file the scan gate can read — yet `git add -A` commits every file beneath it. A brand
    new skill is the mainline classify outcome, so that default would let the one case the
    librarian is most likely to produce walk past the secret scan.
    """
    changed: set[str] = set()
    for path in gitops.git_raw(repo, "diff", "-z", "--name-only", "main...HEAD").split("\0"):
        if path:
            changed.add(path)
    # A rename record spans two fields — `XY <path>\0<original>\0` — and the original is
    # gone from the working tree, so it is consumed and dropped.
    entries = [
        e
        for e in gitops.git_raw(
            repo, "status", "--porcelain", "-z", "--untracked-files=all"
        ).split("\0")
        if e
    ]
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:
            i += 1
        changed.add(path)
    return sorted(changed)


# UTF-8 is what mneme writes, but the gate has to hold over whatever the librarian's
# editor produced. UTF-32 is tried before UTF-16 because a UTF-32 file also decodes
# (into interleaved NULs) under UTF-16, while the reverse practically never happens.
_SCAN_CODECS = ("utf-8-sig", "utf-32", "utf-16")


def _scannable_text(path: Path) -> str | None:
    """Best-effort text for the secret scan — an odd encoding is never a free pass.

    The last resort is a lossy UTF-8 decode: undecodable bytes become replacement
    characters and any ASCII credential sitting among them still reaches the rules.
    Only a file that cannot be read at all yields None.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    for codec in _SCAN_CODECS:
        try:
            return raw.decode(codec)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def _scan_gate(repo: Path, changed: list[str], kind: str) -> None:
    for rel in changed:
        path = repo / rel
        if not path.is_file():
            continue  # deleted or renamed away — nothing left to leak
        text = _scannable_text(path)
        if text is None:
            continue  # unreadable: lint owns shape, the scan owns text
        findings = scan.scan_text(text)
        if scan.has_blockers(findings):
            rules = ", ".join(sorted({f.rule for f in findings if f.severity == scan.BLOCK}))
            raise MnemeError(f"{kind} fails the secret scan: {rel} ({rules})")


def _commit(
    repo: Path, plugin: str, kind: str, unit_lines: list[str], base_sha: str
) -> str:
    """Commit whatever the pass produced; deliver what is already committed unchanged.

    A librarian who commits their own edits on the classify branch — and an index
    regeneration that is then a no-op — leaves nothing in the working tree. That is a
    finished classify pass, not an empty one: the emptiness gate in `finalize` already
    accepted the branch as classifiable because it is ahead of `main`. Demanding a fresh
    commit here would raise into the rollback path and hard-reset the branch away, so the
    one thing the gate acknowledged is the one thing that must never be destroyed.
    """
    gitops.git(repo, "add", "-A")
    if gitops.git(repo, "status", "--porcelain") == "":
        head = gitops.head_sha(repo)
        if head != base_sha:
            return head
        raise MnemeError(f"nothing to commit for this {kind} pass")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"knowledge: {kind} {date}"
    body = "\n".join(f"- {line}" for line in unit_lines)
    message = f"{subject}\n\n{body}\n\nMneme-{kind.capitalize()}: {plugin}\n"
    gitops.git(repo, "commit", "-m", message)
    return gitops.head_sha(repo)


def _finalize(home: Path, cwd: Path, kind: str, *, push: bool = True) -> harvest.HarvestResult:
    """Migrate, regenerate, gate, commit, and offer the branch's work as a PR.

    The rollback and index-regeneration behaviour is deliberately the harvest's own
    (`harvest._abort` / `harvest._regenerate_index`) rather than a second implementation:
    both paths write the same repos under the same PR-only doctrine, and a classify that
    rolled back differently from a harvest would be a second set of edge cases to trust.
    """
    scope, repo = resolve(home, cwd)
    branch = gitops.current_branch(repo)
    if not branch.startswith(_branch_prefix(kind)):
        raise MnemeError(
            f"not on a {kind} branch — run 'mneme {kind} begin' before finalizing"
        )
    # main is only ever read: the rail's branch is the whole deliverable (spec §7.3).
    base_sha = gitops.git(repo, "rev-parse", "main")
    result = harvest.HarvestResult(target=scope.name, branch=branch)

    try:
        dirty = not gitops.is_clean(repo)
        ahead = gitops.head_sha(repo) != base_sha
        moves = _migrate_legacy_facts(repo)
        if not (dirty or ahead or moves):
            raise MnemeError(
                f"nothing to {kind} — no edits were made and no legacy facts needed"
                f" migrating; the {kind} branch has been discarded"
            )
        harvest._regenerate_index(repo)
        issues = lint.lint_repo(repo)
        if lint.has_errors(issues):
            details = "; ".join(f"{i.code} {i.message}" for i in issues if i.severity == "error")
            raise MnemeError(f"{kind} fails repo lint: {details}")
        changed = _changed_files(repo)
        _scan_gate(repo, changed, kind)
    except MnemeError:
        harvest._abort(repo, branch, base_sha)
        raise
    except Exception as e:
        harvest._abort(repo, branch, base_sha)
        raise MnemeError(f"{kind} aborted — {type(e).__name__}: {e}") from e

    moved_dests = {dest for _src, dest in moves}
    result.units = [f"{src} -> {dest} (migrated)" for src, dest in moves] + [
        rel for rel in changed if rel not in moved_dests
    ]

    try:
        result.commit = _commit(repo, scope.name, kind, result.units, base_sha)
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if push and gitops.has_remote(repo):
            gitops.push_branch(repo, branch)
            title = f"knowledge: {kind} {date} ({len(result.units)} changes)"
            result.pr = gitops.open_pr(repo, branch, title, "\n".join(result.units))
        elif not gitops.has_remote(repo):
            result.pr = "no remote — branch left local; merge it or add a remote and push"
        else:
            result.pr = "push skipped (--no-push) — branch left local"
        # The work is handed over, never merged: back to an untouched main.
        gitops.git(repo, "checkout", "main")
    except Exception as e:
        harvest._abort(repo, branch, base_sha)
        raise MnemeError(
            f"{kind} rolled back after the validation gate — {type(e).__name__}: {e};"
            " the repo is back on a clean main"
        ) from e

    record = {
        "kind": kind,
        "target": scope.name,
        "branch": branch,
        "commit": result.commit,
        "pr": result.pr,
        "units": result.units,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        paths.ensure_layout(home)
        with paths.submitted_path(home).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        # The knowledge is committed and the branch is pushed — a ledger write that
        # fails must not turn a delivered pass into an error the user has to undo.
        pass
    return result


def finalize(home: Path, cwd: Path, *, push: bool = True) -> harvest.HarvestResult:
    """Deliver the librarian's reorganization as a pull request."""
    return _finalize(home, cwd, "classify", push=push)


def review_finalize(home: Path, cwd: Path, *, push: bool = True) -> harvest.HarvestResult:
    """Deliver facts extracted from inbound pull requests as mneme's own pull request."""
    return _finalize(home, cwd, "review", push=push)
