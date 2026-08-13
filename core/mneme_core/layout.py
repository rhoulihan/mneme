"""Repo layout migration: a legacy top-level `facts/` becomes the canonical one (spec §5.1).

Facts live inside the router skill (`skills/knowledge-index/facts/`) so the index and the
files it routes to travel as one directory. Repos scaffolded before that was true keep a
top-level `facts/`, and every reader still tolerates it — but a write never does
(`units.facts_write_dir`), because following the old layout is exactly what kept a pre-0.5
repo legacy forever. This module is the other half of that doctrine: the legacy directory
is *migrated*, once, on the next branch a mneme flow creates.

Two properties are load-bearing:

* **Never delete knowledge.** A file the canonical layout does not have is *moved* (with
  `git mv`, so its history follows). A file both layouts carry is *merged* — the legacy
  bullets the canonical file lacks are appended to it — and only then removed. Anything the
  merge cannot key (an unparseable bullet, prose) is carried over verbatim rather than
  dropped: mneme is not entitled to decide that a line a human committed does not count.
* **No commits, no branches.** Callers own both, because PR-only (spec §7.3) is decided one
  level up: a migration on `main` would be the doctrine's one exception, so this function
  never even knows which branch it is on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import gitops, units
from .errors import MnemeError

LEGACY_DIRNAME = "facts"


@dataclass
class MigrationResult:
    """What the migration did, in the exact lines a caller puts in a commit body."""

    moved: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    removed_dir: bool = False

    @property
    def lines(self) -> list[str]:
        return [*self.moved, *self.merged]


def migrate_legacy_facts(repo: Path) -> MigrationResult:
    """Move everything in `repo/facts` under the router skill; return what happened.

    A no-op (empty result, not one byte written) when there is no legacy directory, so a
    caller can run it unconditionally on every branch it creates.
    """
    legacy = repo / LEGACY_DIRNAME
    result = MigrationResult()
    if not legacy.is_dir():
        return result
    canonical = units.facts_write_dir(repo)

    for src in sorted(legacy.iterdir(), key=lambda p: p.name):
        name = src.name
        rel_src = f"{LEGACY_DIRNAME}/{name}"
        rel_dest = f"{units.FACTS_CANONICAL}/{name}"
        # Proven before anything is read or written through it: the destination is built
        # from a legacy FILENAME, which is repo content — whatever a contributor, or a
        # merged pull request, committed into `facts/`. Same proof `harvest._unit_path`
        # makes for candidate-supplied names.
        dest = _contained(canonical, name, rel_src)
        if name == ".gitkeep":
            # A placeholder is not knowledge: the canonical directory it was standing in
            # for is about to exist for real.
            _remove(repo, src, rel_src)
            continue
        if _occupied(dest):
            if name.endswith(".md") and src.is_file() and dest.is_file():
                result.merged.extend(_merge(repo, src, dest, rel_src, rel_dest))
                continue
            raise MnemeError(
                f"cannot migrate {rel_src}: {rel_dest} already exists —"
                " move or merge it by hand, then run the migration again"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        _move(repo, src, dest, rel_src, rel_dest)
        result.moved.append(f"{rel_src} -> {rel_dest}")

    # `git rm` prunes a directory it emptied, so the legacy dir may already be gone.
    if legacy.is_dir():
        remaining = sorted(p.name for p in legacy.iterdir())
        if remaining:
            raise MnemeError(
                f"{LEGACY_DIRNAME}/ still holds {', '.join(remaining)} after migration —"
                " refusing to remove a directory that still carries knowledge"
            )
        legacy.rmdir()
    result.removed_dir = True
    return result


def _contained(canonical: Path, name: str, rel_src: str) -> Path:
    """`canonical/name`, proven to stay inside `canonical` once every link is resolved.

    The resolved form is what matters: a canonical entry that is a *symlink* out of the
    repo would otherwise be appended to (a merge) or replaced (a move) — writing through
    the link to a file no gate in this repo ever sees.
    """
    dest = canonical / name
    try:
        resolved = dest.resolve()
        root = canonical.resolve()
    except OSError as e:
        raise MnemeError(f"cannot migrate {rel_src}: {e.strerror or e}") from e
    if not resolved.is_relative_to(root):
        raise MnemeError(
            f"{rel_src} would land outside {units.FACTS_CANONICAL}/ — refusing to migrate it"
        )
    return dest


def _occupied(dest: Path) -> bool:
    """Is something already there? A broken symlink counts — `exists()` says no."""
    return dest.exists() or dest.is_symlink()


def _spec(rel: str) -> str:
    """`rel` as a git pathspec that matches itself and nothing else.

    Pathspecs glob by default, and a legacy filename is repo content: `facts/a*b.md` would
    make `ls-files` report an untracked file as tracked, and — the dangerous one — make
    `git rm` delete every sibling the glob happens to match, *before* the migration reached
    them. `git mv` takes literal paths already (magic there is a "bad source" fatal).
    """
    return f":(literal){rel}"


def _tracked(repo: Path, rel: str) -> bool:
    return bool(gitops.git(repo, "ls-files", "--", _spec(rel)))


def _move(repo: Path, src: Path, dest: Path, rel_src: str, rel_dest: str) -> None:
    """`git mv` when git knows the path, a plain rename when it does not.

    The distinction is history: a tracked file moved behind git's back is recorded as a
    delete plus an add, and `git log --follow` stops at the move — losing the provenance
    of every fact in a pre-0.5 repo, which is the thing the repo exists to keep.
    """
    if _tracked(repo, rel_src):
        gitops.git(repo, "mv", "--", rel_src, rel_dest)
    else:
        src.rename(dest)


def _remove(repo: Path, src: Path, rel_src: str) -> None:
    if _tracked(repo, rel_src):
        gitops.git(repo, "rm", "-r", "-q", "-f", "--", _spec(rel_src))
    if _occupied(src):
        src.unlink()


def _normalized(line: str) -> str:
    return " ".join(line.split())


def _body_lines(text: str) -> list[str]:
    """Everything below the frontmatter block, or the whole file when there is none.

    An unterminated block is treated as "frontmatter is line 1 only": nothing below it can
    be proven to be metadata, and this function's callers *delete* the file they read, so
    guessing in the lossy direction is the one thing it may not do.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return lines
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[i + 1 :]
    return lines[1:]


def _bullet(line: str) -> units.FactBullet | None:
    """The parsed bullet this line is, or None when no reader can key it."""
    if not line.startswith("- ["):
        return None
    try:
        return units.parse_bullet_line(line, 1)
    except MnemeError:
        return None


def _read_text(path: Path, rel: str) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise MnemeError(f"cannot migrate {rel}: {e}") from e


def _merge(repo: Path, src: Path, dest: Path, rel_src: str, rel_dest: str) -> list[str]:
    """Fold the legacy file's lines into the canonical one, then remove the legacy file.

    Identity is the bullet's SENTENCE, normalized — not the rendered line, and not the
    topic key. Not the line, because `[category]`, `#tags` and the `(verified:)` stamp are
    presentation: the same fact restamped a day later is the same fact, and appending it
    again would duplicate it (`units.fact_text_hash` draws the same line for declines and
    duplicate detection). And not the key, because a topic key is the sentence's first six
    words: "…stale targets for 30 seconds" and "…for 60 seconds" share one, and dropping
    the second is deleting knowledge to tidy a filename — silently, since `apply_batch`
    has no preservation gate to notice. Both are kept and the note says so; a human
    reconciles them in the pull request, which is the whole point of PR-only.
    """
    canonical_body = _body_lines(_read_text(dest, rel_dest))
    canonical_bullets = [b for b in (_bullet(l) for l in canonical_body) if b is not None]
    texts = {_normalized(b.text) for b in canonical_bullets}
    keys = {b.topic_key for b in canonical_bullets}
    seen = {_normalized(l) for l in canonical_body if l.strip()}
    carried: list[str] = []
    bullets = 0
    verbatim = 0
    divergent = 0
    for line in _body_lines(_read_text(src, rel_src)):
        if not line.strip():
            continue
        bullet = _bullet(line)
        if bullet is not None:
            text = _normalized(bullet.text)
            if text in texts:
                continue  # the same knowledge, already canonical: canonical wins
            texts.add(text)
            if bullet.topic_key in keys:
                divergent += 1
            keys.add(bullet.topic_key)
            carried.append(line)
            bullets += 1
            continue
        # No parser can key this line — an unparseable bullet, or prose someone wrote
        # between the bullets. It travels verbatim: the legacy file is about to be deleted,
        # and a line mneme cannot read is still a line a human meant to keep.
        if _normalized(line) in seen:
            continue
        seen.add(_normalized(line))
        carried.append(line)
        verbatim += 1
    if carried:
        _append_lines(dest, carried)
    notes = [f"{rel_src} merged into {rel_dest} ({bullets} bullets)"]
    if divergent:
        notes.append(
            f"{rel_src}: {divergent} bullet(s) share a topic key with a canonical bullet"
            " — both kept, reconcile them in review"
        )
    if verbatim:
        notes.append(f"{rel_src}: {verbatim} unparsed line(s) carried over verbatim")
    _remove(repo, src, rel_src)
    return notes


def _append_lines(path: Path, new_lines: list[str]) -> None:
    """Append lines after the file's last bullet, changing nothing that is already there.

    Deliberately the harvest's own line discipline rather than a second implementation of
    it: a merge is a delta edit like any fact apply, so the BOM, the file's dominant line
    ending, and every untouched byte survive exactly as they do there.
    """
    from . import harvest  # deferred: the branch flows import this module from harvest

    text, bom = harvest._read_raw(path)
    lines = harvest._lines_keepends(text)
    start = harvest._body_start(lines)
    eol = harvest._dominant_eol(lines)
    at = len(lines)
    for i in range(start, len(lines)):
        content, _eol = harvest._split_eol(lines[i])
        if content.startswith("- ["):
            at = i + 1
    if at == len(lines) and lines:
        # Appending past the end: a file that stopped mid-line is terminated first, so the
        # carried line starts on one of its own. Every interior line already ends in an eol.
        tail, tail_eol = harvest._split_eol(lines[-1])
        if not tail_eol:
            lines[-1] = tail + eol
    for offset, line in enumerate(new_lines):
        lines.insert(at + offset, line + eol)
    # newline="": the line endings in `lines` are the file's own, never retranslated.
    path.write_text(bom + "".join(lines), encoding="utf-8", newline="")
