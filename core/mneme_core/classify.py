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

from . import gitops, harvest, layout, lint, paths, scan, templates, units
from .errors import MnemeError

# The kind word is the whole difference between the rails: it names the branch namespace,
# the commit subject, the ledger record, and every message the user reads.
_RAIL_KINDS = ("classify", "review")

# The generated router skill, `skills/knowledge-index/` — the directory the canonical facts
# live inside. Never an integration destination (see `_is_integration_path`).
_INDEX_SKILL_DIR = units.FACTS_CANONICAL.rsplit("/", 1)[0] + "/"


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


def _legacy_conflicts(repo: Path) -> list[str]:
    """Filenames both fact layouts carry — the one thing this rail hands back to the agent.

    Checked BEFORE the finalize rail touches anything, because the rail's failure path is a
    hard reset: raising from inside the migration destroyed the pass's own work (for review,
    an extraction the user had already approved, with nothing staged to retry from) for a
    condition the agent can fix in one edit.

    `layout.migrate_legacy_facts` can now MERGE a colliding pair rather than refuse it, so
    this is no longer the only possible answer — but it is still the right one HERE. The
    collision on this rail is one the agent has just manufactured, in the working tree,
    with the bundle's `facts_dir` naming the file it should have written to; asking it to
    put the bullet in the right file is better than folding two versions together and
    sending a human the difference. The harvest has no such author to ask, so it takes the
    merge.
    """
    legacy = repo / "facts"
    if not legacy.is_dir():
        return []
    canonical = repo / units.FACTS_CANONICAL
    conflicts: list[str] = []
    for src in sorted(p for p in legacy.rglob("*") if p.is_file()):
        rel = src.relative_to(legacy).as_posix()
        if src.name == ".gitkeep":
            continue  # both layouts carrying a placeholder is not a conflict
        if (canonical / rel).exists():
            conflicts.append(rel)
    return conflicts


def _legacy_conflict_error(kind: str, conflicts: list[str]) -> MnemeError:
    merges = "; ".join(
        f"merge facts/{rel} into {units.FACTS_CANONICAL}/{rel} by hand" for rel in conflicts
    )
    return MnemeError(
        f"both fact layouts carry {', '.join(conflicts)} — {merges}, then run"
        f" 'mneme {kind} finalize' again"
    )


def _named_in(rel: str, notes: list[str]) -> bool:
    """Does one of the migration's own notes already name exactly this path?

    A moved or merged file reaches `_changed_files` as well, and reporting it twice in one
    commit body invites a reviewer to look for a second change that does not exist. A bare
    substring test would go wrong the other way and suppress a top-level `README.md` merely
    because some note mentioned `facts/README.md` — the path would vanish from the commit
    body, the PR body and the ledger while staying in the diff.

    So the match is the WHOLE path with its boundaries checked, not a token. Splitting the
    note on whitespace (the previous form) cannot see a path that contains a space, and
    `facts/my deploys.md` is repo content this module's threat model already assumes: it
    was reported twice, once inside its note and again as a bare changed path. The notes
    are prose in three shapes — `a -> b`, `a merged into b (n bullets)`, `a: …` — so a path
    ends at a space, a colon, or the end of the note, and begins at a space or the start.
    """
    for note in notes:
        start = 0
        while (i := note.find(rel, start)) >= 0:
            j = i + len(rel)
            if (i == 0 or note[i - 1] == " ") and (j == len(note) or note[j] in " :"):
                return True
            start = i + 1
    return False


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


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _main_fact_bullets(repo: Path) -> list[tuple[str, str]]:
    """`(file, text)` for every fact bullet committed on `main`.

    Read from the ref rather than the working tree, because the working tree IS the thing
    under suspicion — the pass may already have deleted the file whose loss we are
    checking for. Path selection mirrors `units.fact_files`: the `*.md` directly inside
    either facts layout, canonical first. A bullet `main` already carries malformed is
    skipped; the branch cannot be blamed for damage it did not do.
    """
    prefixes = (f"{units.FACTS_CANONICAL}/", "facts/")
    bullets: list[tuple[str, str]] = []
    listing = gitops.git_raw(repo, "ls-tree", "-r", "-z", "--name-only", "main")
    for rel in listing.split("\0"):
        if not rel.endswith(".md"):
            continue
        if not any(rel.startswith(p) and "/" not in rel[len(p) :] for p in prefixes):
            continue
        try:
            _meta, body = units.parse_frontmatter(gitops.git_raw(repo, "show", f"main:{rel}"))
        except (MnemeError, UnicodeDecodeError):
            # A fact file mneme cannot read was already invisible to lint, the index, and
            # search; making it a wall every finalize hits would not preserve it.
            continue
        for n, line in enumerate(body.splitlines(), start=1):
            if not line.startswith("- ["):
                continue
            try:
                bullets.append((rel, units.parse_bullet_line(line, n).text))
            except MnemeError:
                continue
    return bullets


def _branch_fact_texts(repo: Path) -> set[str]:
    """Normalized text of every fact bullet the branch's working tree still carries."""
    texts: set[str] = set()
    for f in units.fact_files(repo):
        try:
            _meta, body = units.parse_frontmatter(f.read_text(encoding="utf-8-sig"))
        except (MnemeError, OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(body.splitlines(), start=1):
            if not line.startswith("- ["):
                continue
            try:
                texts.add(_normalized(units.parse_bullet_line(line, n).text))
            except MnemeError:
                continue
    return texts


def _is_integration_path(rel: str) -> bool:
    """Is this changed path skill PROSE a fact could have been integrated into?

    "Under `skills/`" is not the test, because the canonical facts directory lives under
    `skills/` too — `skills/knowledge-index/facts/`. Counting a fact FILE as an integration
    destination let the gate be satisfied by the very file whose bullet went missing: a
    bullet rewritten as prose in place, or moved into `facts/archive/` where
    `units.fact_files` (a flat `*.md` glob) never looks again, both left the sentence
    "accounted for" while every reader — lint, the index build, search, the classify
    bundle — had lost it. The rest of the router skill is generated from the fact files, so
    it is no destination either.
    """
    return rel.startswith("skills/") and not rel.startswith(_INDEX_SKILL_DIR)


def _integration_text(repo: Path, changed: list[str]) -> str:
    """One normalized blob of every skill file this pass touched — where facts go to live.

    Only files the pass CHANGED count: an integration is something this branch wrote, and
    scanning the whole repo would let a fact be "accounted for" by a coincidence of
    wording somewhere nobody edited.
    """
    parts: list[str] = []
    for rel in changed:
        if not _is_integration_path(rel):
            continue
        path = repo / rel
        if not path.is_file():
            continue  # deleted on the branch — nothing preserved there
        text = _scannable_text(path)
        if text is not None:
            parts.append(_normalized(text))
    return "\n".join(parts)


def _preservation_gate(repo: Path, changed: list[str], kind: str) -> None:
    """Knowledge on `main` may be moved or integrated by this pass — never dropped.

    A fact is accounted for when its sentence is still a bullet in some fact file, or
    appears verbatim inside a skill file the pass changed. That is deliberately a floor
    and not a judgement of the integration's quality: mneme cannot tell a faithful summary
    from a lossy one, but it can tell that the original sentence still exists somewhere in
    the repo — which is also the better provenance, and what the instructions ask for.
    """
    on_branch = _branch_fact_texts(repo)
    integrated = _integration_text(repo, changed)
    lost = [
        f"{rel}: {text[:80]}"
        for rel, text in _main_fact_bullets(repo)
        if _normalized(text) not in on_branch and _normalized(text) not in integrated
    ]
    if lost:
        raise MnemeError(
            f"{kind} would lose knowledge that is committed on main — "
            + "; ".join(lost)
            + "; facts may move, but never vanish — integrate the content or leave the"
            " fact in place"
        )


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

    # Raised OUTSIDE the guarded block on purpose: nothing has been changed yet, so the
    # branch — and the work on it — survives for the user to fix and finalize again.
    conflicts = _legacy_conflicts(repo)
    if conflicts:
        raise _legacy_conflict_error(kind, conflicts)

    try:
        dirty = not gitops.is_clean(repo)
        ahead = gitops.head_sha(repo) != base_sha
        # The one migration, shared with `harvest.apply_batch` and `mneme migrate`: a rail
        # carrying its own walk would drift from the containment proofs, symlink refusals
        # and never-delete-knowledge merges that only the shared one is tested for.
        migration = layout.migrate_legacy_facts(repo)
        if not (dirty or ahead or migration.lines or migration.removed_dir):
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
        _preservation_gate(repo, changed, kind)
    except MnemeError:
        harvest._abort(repo, branch, base_sha)
        raise
    except Exception as e:
        harvest._abort(repo, branch, base_sha)
        raise MnemeError(f"{kind} aborted — {type(e).__name__}: {e}") from e

    notes = migration.body()
    result.units = notes + [rel for rel in changed if not _named_in(rel, notes)]

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
