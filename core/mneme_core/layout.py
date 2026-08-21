"""Repo layout migration: a legacy top-level `facts/` becomes the canonical one (spec §5.1).

Facts live inside the router skill (`skills/knowledge-index/facts/`) so the index and the
files it routes to travel as one directory. Repos scaffolded before that was true keep a
top-level `facts/`, and every reader still tolerates it — but a write never does
(`units.facts_write_dir`), because following the old layout is exactly what kept a pre-0.5
repo legacy forever. This module is the other half of that doctrine: the legacy directory
is *migrated*, once, on the next branch a mneme flow creates.

Three properties are load-bearing:

* **Never delete knowledge.** A file the canonical layout does not have is *moved* (with
  `git mv`, so its history follows). A file both layouts carry is *merged* — the legacy
  bullets the canonical file lacks are appended to it, and the frontmatter keys it lacks
  are inserted into its header — and only then removed. Anything the merge cannot key (an
  unparseable bullet, prose) is carried over verbatim rather than dropped: mneme is not
  entitled to decide that a line a human committed does not count.
* **Nothing travels through a link — at either end.** The legacy directory, every entry in
  it, and every segment of the canonical directory must really be what they appear to be. A
  `facts` symlink — a back-compat shim pointing at the canonical directory is the natural
  one — makes `iterdir` yield files that live somewhere else entirely, `git ls-files` report
  them as untracked (git does not traverse a symlinked directory), and this module's own
  `unlink`/`rename` then act on the far end: the canonical facts deleted while the note says
  "merged", or a file outside the repo that `_abort`'s `git clean` can never bring back. A
  symlinked *destination* is the mirror image (`_canonical_dir`): every fact is renamed out
  of the repo, `facts/` is deleted, and the result still says "moved". Both shapes are
  decided by repo content — any contributor or merged pull request can commit a symlink —
  so both are refused, never followed.
* **No commits, no branches.** Callers own both, because PR-only (spec §7.3) is decided one
  level up: a migration on `main` would be the doctrine's one exception, so this function
  never even knows which branch it is on.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import gitops, units
from .errors import MnemeError

LEGACY_DIRNAME = "facts"

# Every note together. A pull request body holds ~65 KB and `git commit -m` has a hard
# argument limit; one note per file over a few hundred legacy files reaches both.
_BODY_MAX = 50_000

# How many folded duplicate renderings a merge note writes out before pointing at git. A
# fact bullet is one line, and a note that names 25 of them is still shorter than the diff
# a reviewer would otherwise have to read to find them. The budget below is sized so that
# THIS is the cap that normally binds: when it was 1200 it bound first for any bullet over
# ~46 characters, silently cutting the record to 10-14 lines while the comments here still
# claimed 25 — the note SHRANK in the commit that set out to enlarge it.
_DUPLICATES_SHOWN = 25


def _harvest():
    """The harvest module, imported on use.

    Deferred rather than top-level: the branch flows import *this* module from harvest,
    and the merge borrows harvest's line discipline, so a module-level import is a cycle.
    """
    from . import harvest

    return harvest


@contextmanager
def _guarded(rel: str, what: str) -> Iterator[None]:
    """Filesystem-shape failures read as MnemeError, never as a raw traceback.

    `skills/knowledge-index/facts` occupied by a regular *file*, or a legacy directory that
    turns out not to be removable, makes mkdir/rename/rmdir raise FileExistsError or
    NotADirectoryError. Those are repo-shape problems, not bugs — the same reason
    `harvest.apply_skill` guards its own mkdir: they must surface as MnemeError so a branch
    flow aborts through its guarded rollback path with a message naming the file, instead
    of escaping as an unattributable traceback (or, in a standalone `mneme migrate`, no
    rollback at all).
    """
    try:
        yield
    except OSError as e:
        raise MnemeError(f"cannot migrate {_safe(rel)}: {what} — {e.strerror or e}") from e


@dataclass
class MigrationResult:
    """What the migration did, in the exact lines a caller puts in a commit body."""

    moved: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    removed_dir: bool = False

    @property
    def lines(self) -> list[str]:
        return [*self.moved, *self.merged]

    def body(self, budget: int | None = None) -> list[str]:
        """`lines`, bounded in TOTAL — what a caller should put in a commit or PR body.

        Each note is bounded on its own (`_note`), which is not the same as the body being
        bounded: one note per file times a pre-0.5 repo's several hundred legacy files
        reaches the same two cliffs a single huge note did. Past ~65 KB `gitops.open_pr`
        silently returns its no-PR fallback, which loses the review gate this migration
        exists to feed; past the platform's argument limit `git commit -m` raises E2BIG, an
        OSError that escapes into `harvest._abort` and resets away the very pass that was
        being recorded.
        """
        return bound_body(self.lines, budget, "migration note")


def body_length(lines: list[str]) -> int:
    """The characters `lines` will occupy in a body — the unit `bound_body` budgets in.

    Exposed so a caller can reserve room for lines that must NOT be truncated (a
    retirement is the only record that knowledge left, so dropping one hides a deletion)
    and spend the remainder on lines that may be.
    """
    return sum(len(line) + 1 for line in lines)


def bound_body(lines: list[str], budget: int | None = None, noun: str = "line") -> list[str]:
    """`lines`, truncated so that everything a caller writes into ONE body fits in it.

    Module-level rather than a method because the migration's notes are not the only lines
    that reach a commit body: the rails append one line per changed path to them, and the
    cliff belongs to the BODY, not to whichever list got there first. A bound applied to
    half of what is written is not a bound — a mature pre-0.5 repo produced a 117 KB body
    with the notes alone dutifully held to 50 KB.

    The count of what was left out is always reported, and nothing is lost by leaving it
    out: every line describes a change that is in the diff of the commit it accompanies.
    """
    # Resolved at CALL time, not bound as a default: a default argument freezes the
    # constant at import, so `_BODY_MAX` could not be changed — or mutation-tested —
    # and a bound nothing can falsify is not a bound.
    budget = _BODY_MAX if budget is None else budget

    # The omission line is part of the body, so it is reserved BEFORE the loop rather
    # than appended after it. Appending after meant the one path that truncates was the
    # one path that could exceed the budget — by the length of the sentence explaining
    # that it had not.
    def omission(n: int) -> str:
        return (
            f"…and {n} more {noun}(s), omitted to keep this body inside the"
            " commit and pull request limits — every one of them describes a change"
            " that is in this commit's diff"
        )

    reserve = len(omission(len(lines))) + 1
    kept: list[str] = []
    used = 0
    for line in lines:
        remaining = budget if len(kept) + 1 == len(lines) else budget - reserve
        if used + len(line) + 1 > remaining:
            break
        kept.append(line)
        used += len(line) + 1
    if len(kept) < len(lines):
        kept.append(omission(len(lines) - len(kept)))
    return kept


def migrate_legacy_facts(repo: Path) -> MigrationResult:
    """Move everything in `repo/facts` under the router skill; return what happened.

    A no-op (empty result, not one byte written) when there is no legacy directory, so a
    caller can run it unconditionally on every branch it creates.
    """
    legacy = repo / LEGACY_DIRNAME
    result = MigrationResult()
    if legacy.is_symlink():
        # Refused, not followed: see the module docstring. `is_dir()` is true for a symlink
        # to a directory, so this proof has to come first.
        raise MnemeError(
            f"{LEGACY_DIRNAME}/ is a symlink, not a directory — refusing to migrate through"
            " it: every file it appears to hold really lives at the far end of the link,"
            " where this migration's own `git rm` and `unlink` would delete it. Replace the"
            " link with a real directory (or remove it), then run the migration again"
        )
    if not legacy.is_dir():
        return result
    canonical = _canonical_dir(repo)
    # The SAME resolution `_canonical_dir` used, in the string form git takes. Passing
    # the constant here while resolving the path through `facts_write_dir` is how every
    # `git mv` came to name a directory the migration was not writing into.
    _migrate_into(repo, legacy, canonical, LEGACY_DIRNAME, units.facts_write_rel(repo), result)
    _drop_empty(legacy, LEGACY_DIRNAME)
    result.removed_dir = True
    return result


def _migrate_into(
    repo: Path,
    legacy: Path,
    canonical: Path,
    rel_legacy: str,
    rel_canonical: str,
    result: MigrationResult,
) -> None:
    """Migrate every entry of one legacy directory into its canonical counterpart.

    Recursive so that a subdirectory BOTH layouts carry is merged entry by entry rather
    than refused: two directories of the same name are not a collision between the facts
    inside them, and a repo the code this replaces migrated cleanly must not meet a hard
    error here. A subdirectory only the legacy layout has is still moved whole, in one
    rename, so its history and its contents travel together.
    """
    for src in sorted(legacy.iterdir(), key=lambda p: p.name):
        name = src.name
        rel_src = f"{rel_legacy}/{name}"
        rel_dest = f"{rel_canonical}/{name}"
        # Both ends are proven before anything is read or written through them, because
        # both are repo content — whatever a contributor, or a merged pull request,
        # committed into `facts/`. The DESTINATION is built from a legacy FILENAME (the
        # same proof `harvest._unit_path` makes for candidate-supplied names); the SOURCE
        # is an entry this migration is about to move and delete.
        _from_legacy(src, rel_src)
        dest = _contained(canonical, name, rel_src, rel_canonical)
        if name == ".gitkeep":
            # A placeholder is not knowledge: the canonical directory it was standing in
            # for is about to exist for real.
            _remove(repo, src, rel_src)
            continue
        if _occupied(dest):
            if name.endswith(".md") and src.is_file() and dest.is_file():
                try:
                    result.merged.extend(
                        _note(note)
                        for note in _merge(repo, src, dest, rel_src, rel_dest)
                    )
                except _MergeWouldBury as refused:
                    # The destination cannot hold these facts readably — its own header is
                    # broken, so anything folded into it stops being retrievable. The file
                    # keeps its knowledge and its history under a free name beside it.
                    aside, rel_aside = _aside(canonical, name, rel_canonical, rel_src)
                    # `_decode`, not `_read_text`: one reason a merge is refused is that
                    # this very file is not UTF-8, and the refusal path may not raise on
                    # the file it was invoked to rescue.
                    src_text = _decode(src, rel_src)
                    before = (
                        _readable(src, src_text)
                        if src_text is not None
                        else _Readable({}, "", (), frozenset(), False)
                    )
                    pinned = _pin_stem_topic(src, Path(name).stem, rel_src)
                    _move(repo, src, aside, rel_src, rel_aside)
                    # The refusal path writes (the pin) and renames (the move), and a
                    # rename is a content change here: the stem feeds both the unit id and
                    # the topic. The ids are meant to move and the note says so; a topic
                    # that labels facts is not, and the pin exists to hold it. Measured
                    # rather than assumed, so an unpinnable file is REPORTED as having
                    # moved its topic instead of quietly doing it.
                    moved_text = _decode(aside, rel_aside)
                    after = (
                        _readable(aside, moved_text)
                        if moved_text is not None
                        else _Readable({}, "", (), frozenset(), False)
                    )
                    stranded = _labelled([before]) - _labelled([after])
                    result.moved.append(
                        _note(
                            f"{_safe(rel_src)} -> {_safe(rel_aside)} (kept separate: merging"
                            f" into {_safe(rel_dest)} would have cost {len(refused.lost)}"
                            f" readable item(s) — {_describe_lost(refused.lost)} —"
                            " fix the two by hand and merge them. Note the saved file's unit"
                            f" ids move with its name, from facts/{_safe(name[:-3])}#… to"
                            f" facts/{_safe(rel_aside.rsplit('/', 1)[-1][:-3])}#…"
                            + (
                                f"; `topic: {_safe(Path(name).stem)}` was written into it"
                                " first, so the topic its old filename gave it survives the"
                                " rename"
                                if pinned
                                else ""
                            )
                            + (
                                f". Its facts moved to topic “{_safe(after.topic)}”: the"
                                " topic"
                                f" “{'”, “'.join(_safe(t) for t in sorted(stranded))}” came"
                                " from its filename and could not be written into its"
                                " header — set `topic:` by hand"
                                if stranded
                                else ""
                            )
                            + ")"
                        )
                    )
                continue
            if src.is_dir() and dest.is_dir() and not dest.is_symlink():
                # Both layouts carry this subdirectory. `dest` must be a real directory:
                # recursing through a link would hand the next level a base that resolves
                # elsewhere, which is exactly the hole `_canonical_dir` closes at the top.
                _migrate_into(repo, src, dest, rel_src, rel_dest, result)
                _drop_empty(src, rel_src)
                continue
            raise MnemeError(
                f"cannot migrate {_safe(rel_src)}: {_safe(rel_dest)} already exists —"
                " move or merge it by hand, then run the migration again"
            )
        with _guarded(rel_src, f"cannot create {rel_canonical}/"):
            dest.parent.mkdir(parents=True, exist_ok=True)
        _move(repo, src, dest, rel_src, rel_dest)
        result.moved.append(f"{_safe(rel_src)} -> {_safe(rel_dest)}")


def _aside(canonical: Path, name: str, rel_canonical: str, rel_src: str) -> tuple[Path, str]:
    """A free name beside `name` in the canonical directory, for a file that cannot merge.

    `<stem>-legacy.md`, then `-2`, `-3`… Every candidate goes through `_contained`, because
    the stem still comes from a legacy filename; the suffix is appended to a name that
    proof has already accepted, so it cannot introduce a segment of its own.

    A HYPHEN, not a dot. Every unit id this file mints is `facts/<stem>#<key>`, and the
    write rail proves that stem is kebab-case (`harvest.apply_fact` -> `_fact_path` ->
    `_unit_path`), so `t.legacy` would mint ids that `mneme share apply` can never target —
    the migration deliberately minting unaddressable knowledge, on the one third of
    collisions it declines. `t-legacy` reads the same and stays writable.
    """
    stem = name[: -len(".md")] if name.endswith(".md") else name
    for attempt in range(1, 100):
        suffix = "-legacy" if attempt == 1 else f"-legacy-{attempt}"
        candidate = f"{stem}{suffix}.md"
        path = _contained(canonical, candidate, rel_src, rel_canonical)
        if not _occupied(path):
            return path, f"{rel_canonical}/{candidate}"
    raise MnemeError(
        f"cannot migrate {_safe(rel_src)}: {rel_canonical}/{_safe(stem)}-legacy*.md are all taken —"
        " reconcile them by hand, then run the migration again"
    )


def _pin_stem_topic(path: Path, stem: str, rel: str) -> bool:
    """Write the topic a file was getting from its old filename, before the name changes.

    Every reader resolves a fact file's topic as `meta.get("topic", stem)` — the index
    `name` column, the router's routing table, the classify bundle — so the filename is a
    value source, and renaming a file that has no `topic:` key of its own silently moves
    every fact in it to a different topic. Renaming is exactly what the refusal path does,
    so the implicit value is made explicit first: an insert, not a rewrite, and only when
    the header the reader sees is one it can read (a file it already rejects has no
    retrievable topic to preserve).
    """
    text = _decode(path, rel)
    if text is None:
        return False  # not UTF-8: no header to pin a topic into, and nothing readable to lose
    try:
        meta, _body = units.parse_frontmatter(text)
    except MnemeError:
        return False
    if "topic" in meta:
        return False
    h = _harvest()
    raw, bom = h._read_raw(path)
    eol = h._dominant_eol(h._lines_keepends(raw))
    line = f"topic: {stem}" + eol
    # Written in the PARSER's line space, because the insert has to land inside the block
    # the parser recognises. Deciding that with this module's CR/LF-only splitting put a
    # brand-new block ABOVE a header the parser was already reading whenever the two
    # disagreed (a `\x0b` in the opening delimiter line is enough), demoting every key in
    # it to prose — a refusal path destroying the metadata it was invoked to protect.
    # `splitlines(keepends=True)[0]` is the opening delimiter exactly as the parser sees
    # it, terminator included, so appending after it leaves every other byte untouched.
    opening = raw.splitlines(keepends=True)
    if opening and opening[0].strip() == "---":
        pinned = opening[0] + line + raw[len(opening[0]) :]
    else:
        pinned = "---" + eol + line + "---" + eol + raw
    with _guarded(rel, "cannot pin its topic before renaming it"):
        path.write_text(bom + pinned, encoding="utf-8", newline="")
    # The pin is a write on the refusal path, so it is measured like every other write here
    # — and reverted rather than trusted if it costs anything, since moving the file aside
    # unpinned loses at most the stem-derived topic while a bad pin can hide the whole file.
    if _lost([_readable(path, text)], [_readable(path, _read_text(path, rel))]):
        with _guarded(rel, "cannot restore it after abandoning the topic pin"):
            path.write_text(bom + raw, encoding="utf-8", newline="")
        return False
    return True


def _drop_empty(legacy: Path, rel: str) -> None:
    """Remove a legacy directory the migration emptied; refuse while anything remains.

    `git rm` prunes a directory it emptied, so the directory may already be gone.
    """
    if not legacy.is_dir():
        return
    remaining = sorted(p.name for p in legacy.iterdir())
    if remaining:
        raise MnemeError(
            f"{_safe(rel)}/ still holds {_join_capped(remaining, ', ', 400, cap=80)[0]} after migration —"
            " refusing to remove a directory that still carries knowledge"
        )
    with _guarded(f"{rel}/", "cannot remove the legacy directory"):
        legacy.rmdir()


def _canonical_dir(repo: Path) -> Path:
    """`units.facts_write_dir(repo)`, proven to be reachable without traversing a link.

    The destination half of "nothing travels through a link", and the half `_contained`
    cannot make: it proves a destination stays under the canonical directory *as resolved*,
    so a canonical directory that is itself a link resolves to the far end and every
    destination is trivially "contained" there. Followed, the migration renames every fact
    out of the repo, deletes `facts/`, and reports `moved` — and the caller's `git add -A`
    stages a bare deletion of knowledge with no counterpart anywhere in the tree.
    `skills/knowledge-index/facts` is repo content like any other path (a contributor, or a
    merged pull request, can commit any segment of it as a symlink), so every segment below
    the repo root is proven here, once, before the first entry is read.

    The repo root itself is deliberately not checked: it is the caller's own path, a clone
    under a symlinked parent is ordinary, and every proof below is made relative to it.
    """
    canonical_rel = units.facts_write_rel(repo)
    walked = repo
    for part in Path(canonical_rel).parts:
        walked = walked / part
        if walked.is_symlink():
            rel = walked.relative_to(repo).as_posix()
            raise MnemeError(
                f"{rel} is a symlink, not a directory — refusing to migrate into"
                f" {canonical_rel}/ through it: every fact would be renamed to the"
                " far end of the link, outside the gates this repo runs and outside what a"
                " rollback can reach, while the migration reported it moved. Replace the"
                " link with a real directory (or remove it), then run the migration again"
            )
    return units.facts_write_dir(repo)


def _from_legacy(src: Path, rel_src: str) -> None:
    """Prove the entry really lives in the legacy directory before it is moved or deleted.

    One check is enough for the whole source side: the legacy directory itself is already
    proven not to be a link, so every entry `iterdir` yields is a real child of it, and the
    only way one of them can name a file elsewhere is by being a symlink itself. Refused
    rather than followed — `_remove` would `unlink` the far end, and a fact deleted with
    "merged" written next to it is the one outcome this module exists to prevent.
    """
    if src.is_symlink():
        raise MnemeError(
            f"cannot migrate {_safe(rel_src)}: it is a symlink, and the file it points at is not"
            " mneme's to move or delete — replace it with the file itself (or remove it),"
            " then run the migration again"
        )


def _contained(canonical: Path, name: str, rel_src: str, rel_canonical: str) -> Path:
    """`canonical/name`, proven to stay inside `canonical` once every link is resolved.

    The resolved form is what matters: a canonical entry that is a *symlink* out of the
    repo would otherwise be appended to (a merge) or replaced (a move) — writing through
    the link to a file no gate in this repo ever sees.

    Resolving `canonical` too is only sound because the base is proven link-free first
    (`_canonical_dir`, and the same proof on each recursion): otherwise the base would
    resolve to wherever the link points and containment could never fail.
    """
    dest = canonical / name
    try:
        resolved = dest.resolve()
        root = canonical.resolve()
    except OSError as e:
        raise MnemeError(f"cannot migrate {_safe(rel_src)}: {e.strerror or e}") from e
    if not resolved.is_relative_to(root):
        raise MnemeError(
            f"{_safe(rel_src)} would land outside {rel_canonical}/ — refusing to migrate it"
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
        with _guarded(rel_src, f"cannot move it to {rel_dest}"):
            src.rename(dest)


def _remove(repo: Path, src: Path, rel_src: str) -> None:
    if _tracked(repo, rel_src):
        gitops.git(repo, "rm", "-r", "-q", "-f", "--", _spec(rel_src))
    if _occupied(src):
        with _guarded(rel_src, "cannot remove it"):
            src.unlink()


def _normalized(line: str) -> str:
    return " ".join(line.split())


# How much of one repo-derived value a note may carry. Long enough for a real topic, a
# real path and a real fact sentence; short enough that a note stays a note.
_NOTE_VALUE_MAX = 160

# The whole of one note. A pull request body holds ~65 KB across every note the migration
# emits, and `git commit -m` has a hard argument limit below that, so one note may not eat
# the budget the rest need.
_NOTE_MAX = 10_000

# A folded duplicate's own rendering gets a larger allowance than an ordinary value: it is
# the only remaining view of that line's tags and stamp outside the diff, and tags and the
# `(verified:)` stamp sit at the END of a bullet, so a short cap removes exactly the part
# the note exists to preserve.
_DUPLICATE_LINE_MAX = 300
_DUPLICATES_BUDGET = _DUPLICATES_SHOWN * _DUPLICATE_LINE_MAX


def _safe(value: object, cap: int = _NOTE_VALUE_MAX) -> str:
    """A value read out of the repo, made fit to go into a commit body and a PR body.

    Every note this module returns is spliced into the commit body and, through
    `gitops.open_pr`, into the pull request body — the artifact a human reads to decide
    whether the migration was safe. A `topic:` value and a filename are both repo content,
    and this module's own docstring is explicit that `facts/` is untrusted: a contributor,
    or a merged pull request, can commit `topic: "deploys\\n\\nMneme-Review: approved"`,
    and `units._unescape` turns that `\\n` into a real newline. One note then becomes nine
    physical lines carrying a forged trailer and an invented finding — the `- ` prefix a
    caller puts on a note covers only the first of them.

    Collapsing the whitespace is what `_carry_meta` already did to a value before putting
    it in a note; the cap is what a folded scalar needs, since a 105 KB `topic: >` produced
    a 208 KB note (the value appears twice), past the pull request body limit, which
    degrades `open_pr` to its no-PR fallback and loses the review gate entirely.
    """
    text = _normalized(str(value))
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _elided(text: str, cap: int) -> str:
    """Cap a line by removing its MIDDLE, because a fact bullet carries value at both ends.

    `- [gotcha] <sentence> #tag (verified: 2026-01-01)`: the category leads and the tags
    and stamp trail, and for a folded duplicate those ends are the entire reason the note
    exists — the sentence itself is still in the canonical file, and only the rendering is
    gone. Cutting the tail off (a plain cap) therefore deletes exactly the part worth
    keeping, which is what the first version of this did.
    """
    text = _normalized(text)
    if len(text) <= cap:
        return text
    head = (cap - 1) * 2 // 3
    return text[:head] + "…" + text[-(cap - 1 - head) :]


def _join_capped(
    items: list,
    sep: str,
    budget: int,
    cap: int = _NOTE_VALUE_MAX,
    limit: int | None = None,
    elide: bool = False,
) -> tuple[str, int]:
    """Join what fits in `budget` characters; return it and how many were left out.

    Capping each VALUE is not the same as capping a NOTE, and the difference is a
    multiplicity the repo controls: a legacy/canonical pair disagreeing on 1200 frontmatter
    keys produced a 94 KB note out of 1200 individually-tiny values, and one bullet with
    12,000 tags produced 85 KB. Past ~65 KB `gitops.open_pr` silently falls back to no PR
    at all — losing the review gate this migration exists to feed — and past ~128 KB
    `git commit -m` raises E2BIG, an OSError that reaches `harvest._abort` and resets the
    pass's own work away. Both inputs are `facts/` content under this module's own declared
    threat model, and both are also just what a large real repo looks like.
    """
    shown: list[str] = []
    used = 0
    for item in items:
        text = _elided(str(item), cap) if elide else _safe(item, cap)
        # The separator is charged only when there is something to separate from, so `used`
        # is exactly `len(sep.join(shown))`. Charging it unconditionally reserved room for a
        # separator after the LAST item, which does not exist — every list stopped one entry
        # short of its budget, silently and forever.
        extra = len(text) + (len(sep) if shown else 0)
        if (limit is not None and len(shown) >= limit) or used + extra > budget:
            break
        shown.append(text)
        used += extra
    return sep.join(shown), len(items) - len(shown)


def _differing_text(differing: list[str]) -> str:
    """The "X (kept) vs Y" entries, bounded: one entry per key, and keys are repo content.

    1200 disagreeing keys — every value individually tiny and already capped — assembled a
    94 KB note. Reconciling that many by hand is not what this note is for; naming the
    first few and the count is.
    """
    joined, omitted = _join_capped(differing, "; ", 600, cap=200)
    return joined + (f"; and {omitted} more key(s)" if omitted else "")


def _note(text: str) -> str:
    """One note, guaranteed one line and guaranteed bounded — by construction, not review.

    The per-list budgets above keep every note well under this; this is the backstop that
    holds when a future note forgets one, because "every value is capped" has already been
    true here while a note was 94 KB long.
    """
    return _safe(text, _NOTE_MAX)


def _line_contents(text: str) -> list[str]:
    """`text` as lines, broken on CR/LF only, with the line endings stripped.

    Deliberately not `str.splitlines`, which also breaks on \\x0b, \\x0c, \\u2028 and
    friends — inside a fact bullet those are data (`harvest._LINE_RE` exists for exactly
    this reason). Splitting there would carry one legacy bullet into the canonical file as
    two lines with the separator byte deleted: a silent edit to a line this module promised
    to move verbatim.
    """
    h = _harvest()
    return [h._split_eol(line)[0] for line in h._lines_keepends(text)]


def _opens_frontmatter(lines: list[str]) -> bool:
    """Does the file start with a frontmatter delimiter (terminated or not)?"""
    return bool(lines) and lines[0].strip() == "---"


def _frontmatter_end(lines: list[str]) -> int | None:
    """Index of the line closing a leading frontmatter block, or None when there is none.

    An unterminated block is not a block: nothing below the opening delimiter can be proven
    to be metadata, and this module *deletes* the file it reads, so guessing in the lossy
    direction is the one thing it may not do. It is not the only thing stopping that,
    though, and the docstring used to imply it was: mutating this to return `len(lines)` —
    treating a whole unterminated file as metadata — leaves the suite green, because
    `_reader_accepts`'s all-or-nothing check then refuses to promote a block the parser
    rejects and the lines travel into the body anyway, and `_lost` measures the result
    regardless. Two guards behind it, and the honest reading is that this one keeps the
    common path simple rather than being load-bearing on its own. `harvest._body_start` raises there instead,
    which is right for a single fact apply and wrong here — one malformed file would wedge
    every branch flow with an error naming no file, while lint (MN010) already reports that
    file by name.
    """
    if not _opens_frontmatter(lines):
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    return None


def _sections(text: str) -> tuple[list[str] | None, list[str]]:
    """(frontmatter lines, body lines). The frontmatter is None when the file has no block.

    None and `[]` are different answers: an empty block has somewhere for a carried key to
    land, a missing one does not.
    """
    lines = _line_contents(text)
    end = _frontmatter_end(lines)
    if end is None:
        return None, lines
    return lines[1:end], lines[end + 1 :]


_META_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):")


def _reader_accepts(meta_lines: list[str]) -> bool:
    """Would `units.parse_frontmatter` accept a block built from exactly these lines?

    The one invariant this module owes the repository is that a merge never hands mneme's
    own reader a file the reader rejects — a fact file that does not parse is invisible to
    lint, the index, search and the classify bundle, so knowledge that was retrievable
    before the migration is not after it, however intact the bytes are.

    Asking the parser is the only way to hold that invariant. Two earlier attempts derived
    a second, looser grammar here instead (`_META_KEY_RE` plus `_meta_blocks`' rule that an
    unrecognised line belongs to the PRECEDING key) and both leaked: a stray line was
    treated as unkeyable in first position and keyed in every other, so `topic: t` followed
    by prose, a flush-left list under `tags:`, a tab-indented continuation and a malformed
    nested block all still entered the header. "Keyed" was never the same predicate as
    "parseable", and only the parser knows the difference.
    """
    text = "---\n" + "".join(line + "\n" for line in meta_lines) + "---\n"
    try:
        units.parse_frontmatter(text)
    except MnemeError:
        return False
    return True


@dataclass(frozen=True)
class _Readable:
    """One fact file as its READERS see it — the whole basis of this module's guard.

    Six attempts at that guard each measured something strictly smaller than the property
    demanded (bullets only, then metadata keys, then metadata values, then a re-derivation
    of the topic), and each time a real reader was still projecting something the check
    could not see. So nothing here is derived twice: `rows` is keyed and shaped exactly
    like `mneme_index.build._fact_rows`, `topic` uses the readers' own `meta.get("topic",
    stem)` lookup (`or` is not that lookup — a present-but-empty `topic:` stays empty for
    every reader and would map back to the stem here), and `parses` is the parser's own
    verdict rather than a second grammar.

    `path` matters as much as `text`: the same bytes under a different name are different
    rows, because the stem feeds both the unit id and the topic.
    """

    rows: dict[str, tuple]
    topic: str
    header: tuple[str, ...]
    lines: frozenset[str]
    parses: bool


def _parser_header(text: str) -> tuple[str, ...]:
    """The raw lines the PARSER treats as frontmatter, in the parser's own line space.

    Not `_sections`, which splits on CR/LF only because a fact bullet may legitimately
    contain `\\x0b` and friends as data. That difference is right for deciding what to
    CARRY (a line must move verbatim) and wrong for deciding what a reader can SEE: where
    the two disagree, `str.splitlines` is the one whose answer the readers use.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return tuple(lines[1:i])
    return ()


def _readable(path: Path, text: str) -> _Readable:
    lines = frozenset(_normalized(line) for line in text.splitlines() if line.strip())
    header = _parser_header(text)
    try:
        meta, body = units.parse_frontmatter(text)
    except MnemeError:
        return _Readable({}, "", header, lines, False)
    topic = str(meta.get("topic", path.stem))
    rows: dict[str, tuple] = {}
    # `body.splitlines()` and `line.startswith("- [")`, because that is what all three
    # readers do — not this module's own CR/LF-only splitting, which would see one line
    # where `build._fact_rows` sees two.
    for line in body.splitlines():
        if not line.startswith("- ["):
            continue
        bullet = _bullet(line)
        if bullet is None:
            continue
        # `setdefault`, not assignment: `index_tree` keeps the FIRST row for a unit id and
        # reports the rest as duplicates, so first-wins is the retrievable one here too.
        rows.setdefault(
            units.fact_unit_id(path.stem, bullet.text),
            (
                topic,
                _normalized(bullet.text),
                bullet.category,
                tuple(bullet.tags),
                bullet.verified or "",
            ),
        )
    return _Readable(rows, topic, header, lines, True)


def _dedup(files: list[_Readable]) -> dict[str, tuple]:
    """The rows the INDEX can retrieve across these files, first file winning.

    Unit ids are `facts/<stem>#<key>` — they carry the file's STEM, not its directory — so
    a legacy file and the canonical file it collides with produce the same ids, and
    `index_tree` stores only the first, reporting the second in `stats.skipped` as a
    duplicate. Two renderings of one sentence under one stem are therefore never both in
    the index, and folding one away costs `mneme search`, `list_facts` and the router
    nothing. Measuring them as two (an earlier attempt) is what made the migration refuse
    the majority of ordinary collisions.

    Scoped deliberately, because it is NOT true of every reader. `classify._fact_entries`
    and `cli._verify_cmd` walk `units.fact_files` and emit one entry per bullet per FILE,
    with no dedup, so both renderings reach the librarian bundle and the staleness report
    while the two files coexist — and after this merge only the canonical one does. That
    cost is accepted rather than overlooked: Plan 12's constraint is topic-key dedup with
    the canonical file winning a collision, `units.fact_text_hash` already defines a fact's
    identity as its sentence alone (so declines and duplicate detection draw the same
    line). The fold is reported with the folded line itself written into the note,
    abbreviated only past `_DUPLICATE_LINE_MAX` — tags and the `(verified:)` stamp sit at
    the END of a bullet, so a short cap removes exactly the part worth keeping — and the
    deleted file is in the pull request diff either way.

    What may NOT be lost is a row the index
    holds, a topic that labels facts, or a frontmatter line — those are `_lost`'s strict
    properties.
    """
    rows: dict[str, tuple] = {}
    for f in files:
        for uid, row in f.rows.items():
            rows.setdefault(uid, row)
    return rows


def _labelled(files: list[_Readable]) -> set[str]:
    """Topics that LABEL at least one fact — the routing table's rows, per `scaffold`.

    A topic with no facts under it names a file and nothing else, and a merge is entitled
    to make one filename stop existing. A topic that labels facts is knowledge: it is what
    `regenerate_index_skill` puts in the router's table and what `list_facts(topic=…)`
    filters on, so an agent that could reach a fact through it must still be able to.
    """
    return {f.topic for f in files if f.rows}


def _lost(before: list[_Readable], after: list[_Readable]) -> set[tuple[str, str]]:
    """What the readers could retrieve before and cannot after. Empty means nothing broke.

    Four properties, each one a reader's own view rather than a model of it: the file still
    parses (a fact file that does not is invisible to lint, the index, search and the
    classify bundle, however intact its bytes are); every row the INDEX holds is still
    held, unchanged in every column a reader filters on (`_dedup` — which is where the
    scope of that word is argued, and where the one thing this does not protect is named);
    every topic that labelled a fact still labels one; and every frontmatter line still
    exists SOMEWHERE in the result.

    That last one is deliberately looser than the others. A key whose value the two files
    disagree on cannot stay a key — one file, one value — so `_carry_meta` demotes the
    legacy line into the body and notes both values for the reviewer. The line is still
    there to reconcile from, and no reader ever projected `owner:` into a row, so demoting
    it costs a reviewer nothing and refusing over it cost the migration two thirds of its
    ordinary merges.
    """
    lost: set[tuple[str, str]] = set()
    if any(f.parses for f in before) and not all(f.parses for f in after):
        lost.add(("file", "the result would not parse as a fact file, hiding everything in it"))
    rows_after = _dedup(after)
    for uid, row in _dedup(before).items():
        if rows_after.get(uid) != row:
            lost.add(("fact", _render(row)))
    for topic in _labelled(before) - _labelled(after):
        lost.add(("topic", f"topic “{_safe(topic)}”, which labels facts in the routing table"))
    lines_after = frozenset().union(*(f.lines for f in after)) if after else frozenset()
    for f in before:
        for line in f.header:
            if line.strip() and _normalized(line) not in lines_after:
                lost.add(("frontmatter", _safe(line)))
    return lost


def _render(row: tuple) -> str:
    """A fact row written the way its author wrote it, so a reviewer can tell two apart.

    Naming only the sentence and the category (the fourth attempt) named the two fields
    most likely to be IDENTICAL to the line that survived, and never the field that
    actually differed — a reviewer read "would have lost “X” [gotcha]" while “X” [gotcha]
    was plainly still in the file.
    """
    topic, text, category, tags, verified = row
    rendered = f"“{_safe(text)}” [{_safe(category)}]"
    if tags:
        joined, omitted = _join_capped([f"#{t}" for t in tags], " ", 120, cap=40)
        rendered += f" {joined}" + (f" (+{omitted} more tags)" if omitted else "")
    if verified:
        rendered += f" (verified: {_safe(verified)})"
    return f"{rendered} under topic “{_safe(topic)}”"


def _meta_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Frontmatter lines grouped under the key each belongs to, `""` for lines under none.

    A key owns its own line plus everything indented under it (a list, a nested map, a
    folded scalar) — `units._parse_block`'s shape — and the block travels as raw text,
    because a key that moves must land in the canonical file exactly as its author wrote it.
    """
    blocks: list[tuple[str, list[str]]] = []
    for line in lines:
        m = _META_KEY_RE.match(line)
        if m:
            blocks.append((m.group(1), [line]))
        elif blocks:
            blocks[-1][1].append(line)
        else:
            blocks.append(("", [line]))
    return blocks


def _carry_meta(
    canonical: list[str], legacy: list[str], rel_src: str, rel_dest: str
) -> tuple[list[str], list[str], list[str]]:
    """(keys to insert into the canonical block, lines to carry into the body, notes).

    The legacy file is about to be deleted, and an `owner:` or `sources:` key on it is a
    line a human committed exactly as much as a bullet is — the merge is not entitled to
    decide it does not count, which is precisely what reading only the body did. A key the
    canonical file already carries is left alone (canonical wins, as it does for a bullet);
    one whose value differs cannot stay a key, because one file has one header, so its line
    is demoted into the body and *reported* rather than silently dropped: both values are
    knowledge and only a human can pick.

    Demotion is a real answer rather than a consolation because `topic` is the only fact
    file key any reader projects (`build._fact_rows`, `scaffold.regenerate_index_skill`,
    `classify._fact_entries` and `lint.lint_fact_file` — every `parse_frontmatter` caller
    that reads a fact file, and all four read exactly one key; MN009 checks that this same
    key is PRESENT, which a demotion would break, so `topic` is the one key `_carry_meta`
    must never demote). Nothing retrieves `owner:` into a row,
    an FTS column or the classify bundle, so that line is content a human reads, and it
    reads the same three lines lower. `topic` is the exception and is never demoted
    quietly: `_lost` refuses the whole merge when a topic that labels facts would stop
    labelling them. Treating every key like `topic` (the sixth attempt) refused two thirds
    of ordinary collisions to protect values nothing was retrieving.

    Anything the frontmatter grammar cannot key travels with the body instead of into the
    block, so a stray line in a legacy header can never make the canonical header
    unparseable.
    """
    have: dict[str, str] = {}
    for key, block in _meta_blocks(canonical):
        have.setdefault(key, _normalized(" ".join(block)))
    carried: list[str] = []
    body: list[str] = []
    keys: list[str] = []
    differing: list[str] = []
    for key, block in _meta_blocks(legacy):
        if not key:
            body.extend(block)
            continue
        if key in have:
            legacy_value = _normalized(" ".join(block))
            if have[key] != legacy_value:
                # Both values are knowledge — the same rule that keeps two bullets sharing
                # a topic key. The canonical one wins the HEADER; the legacy block travels
                # into the body rather than being discarded, because discarding it deleted
                # whatever `_meta_blocks` had attached to that key as well (a stray line, a
                # list under it, a comment a human wrote) from a file this merge then
                # removes. The note names both values so the reviewer can reconcile them.
                body.extend(block)
                differing.append(
                    f"{_safe(have[key])} (kept) vs {_safe(legacy_value)} (from {_safe(rel_src)})"
                )
            continue
        carried.extend(block)
        keys.append(key)
    # The same all-or-nothing guard the `new_block` branch applies, for the same reason:
    # `_meta_blocks` will happily hand back a "key" whose block contains a line the parser
    # rejects, and inserting that into the canonical header breaks a file that read fine a
    # moment ago. The keys are only carried if the reader accepts the header they produce.
    demoted = False
    if carried and not _reader_accepts(canonical + carried):
        body.extend(carried)
        carried, keys = [], []
        demoted = True

    notes: list[str] = []
    if demoted:
        notes.append(
            f"{_safe(rel_src)}: its header is not readable as frontmatter alongside {_safe(rel_dest)}'s,"
            " so those lines travelled into the body — nothing was dropped; promote them"
            " in review if they were meant as metadata"
        )
    elif keys:
        carried_text, omitted_keys = _join_capped(keys, ", ", 400, cap=60)
        notes.append(
            f"{_safe(rel_src)}: frontmatter key(s) carried over: {carried_text}"
            + (f" and {omitted_keys} more" if omitted_keys else "")
        )
    if differing:
        notes.append(
            f"{_safe(rel_src)}: frontmatter differs from {_safe(rel_dest)} —"
            f" {_differing_text(differing)} — reconcile in review"
        )
    return carried, body, notes


class _MergeWouldBury(Exception):
    """Folding the legacy file into this canonical one would cost retrievable knowledge.

    Raised only after the merge has been attempted and measured, because the condition is
    a property of the RESULT, not of either input: a canonical file whose own header the
    reader rejects yields nothing, so bullets merged into it are buried — and that happens
    with a perfectly well-formed legacy file, or one with no header at all, which is why no
    amount of guarding the legacy side can catch it. The caller moves the file aside
    instead, where its facts stay readable and a human reconciles the two in the PR.
    """

    def __init__(self, lost: set[tuple[str, str]]):
        super().__init__("the merge would bury retrievable facts")
        self.lost = lost


def _describe_lost(lost: set[tuple[str, str]]) -> str:
    """Name what a refusal protected, in the terms a reviewer reconciles by.

    The notes a refused merge would have produced are discarded along with the merge, so
    this is the only line in the pull request describing what the migration declined to
    decide — a bare count names neither the topic nor the fact nor either value, and leaves
    a reviewer to diff for it.
    """
    shown = [f"{kind}: {what}" for kind, what in sorted(lost)[:3]]
    more = f"; and {len(lost) - len(shown)} more" if len(lost) > len(shown) else ""
    return "; ".join(shown) + more


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
        raise MnemeError(f"cannot migrate {_safe(rel)}: {e}") from e


def _decode(path: Path, rel: str) -> str | None:
    """The file's text, or None when it is not UTF-8 at all.

    A `.md` under `facts/` that is not valid UTF-8 is repo content like any other, and
    every other reader tolerates it: `build._read_unit_text` records it in `skipped`,
    `classify._fact_entries` notes it and continues. Only this module used to raise, which
    once the migration runs on every branch flow would wedge every classify, review and
    share finalize on one undecodable byte. It cannot be MERGED — there is no text to fold
    — so it takes the same aside path as every other file this module cannot merge.

    An OSError still raises: a permission or I/O failure is a problem with the machine, not
    a property of the file, and guessing past it is how a migration reports "moved" for a
    file it never read.
    """
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return None
    except OSError as e:
        raise MnemeError(f"cannot migrate {_safe(rel)}: {e}") from e


def _merge(repo: Path, src: Path, dest: Path, rel_src: str, rel_dest: str) -> list[str]:
    """Fold the legacy file's lines into the canonical one, then remove the legacy file.

    Identity is the bullet's SENTENCE, normalized — not the rendered line, and not the
    topic key. (A DECLARED departure from Plan 12's "topic-key dedup" wording, in the
    direction that constraint's own next sentence demands — "a merge that would drop a
    bullet is a bug, not a resolution". A topic key is derived FROM the sentence, so
    sentence-identity is the finer relation: it can only keep more, never less.) Not the
    line, because `[category]`, `#tags` and the `(verified:)` stamp are
    presentation: the same fact restamped a day later is the same fact, and appending it
    again would duplicate it (`units.fact_text_hash` draws the same line for declines and
    duplicate detection). And not the key, because a topic key is the sentence's first six
    words: "…stale targets for 30 seconds" and "…for 60 seconds" share one, and dropping
    the second is deleting knowledge to tidy a filename — silently, since `apply_batch`
    has no preservation gate to notice. Both are kept and the note says so; a human
    reconciles them in the pull request, which is the whole point of PR-only.

    Frontmatter travels on the same terms (`_carry_meta`): the legacy file is deleted at
    the end of this function, so a key only it carries has to land in the canonical header
    or it is gone.
    """
    with _guarded(rel_dest, "cannot read it"):
        dest_before = dest.read_bytes()  # the exact bytes to put back if the merge is refused
    dest_text = _decode(dest, rel_dest)
    src_text = _decode(src, rel_src)
    if src_text is None or dest_text is None:
        # Nothing to fold, in either direction: refused like any other unmergeable pair, so
        # the legacy file keeps its bytes and its history beside the canonical one.
        unreadable = rel_src if src_text is None else rel_dest
        raise _MergeWouldBury({("bytes", f"{_safe(unreadable)} is not valid UTF-8")})
    # Measured on the way in, checked on the way out: the merge is allowed to move a fact
    # anywhere, and not allowed to make one stop being readable. The canonical file goes
    # FIRST, because that is the order `units.fact_files` yields and therefore the order
    # `index_tree` resolves a duplicate unit id in — the canonical rendering is the
    # retrievable one, before the merge and after it.
    readable_before = [_readable(dest, dest_text), _readable(src, src_text)]
    canonical_meta, canonical_body = _sections(dest_text)
    legacy_meta, legacy_body = _sections(src_text)
    carried_meta: list[str] = []
    new_block: list[str] = []
    meta_notes: list[str] = []
    demoted_meta = 0
    if canonical_meta is None and legacy_meta and not _opens_frontmatter(canonical_body):
        # The canonical file has no header at all. Dropping the legacy keys into its body
        # would leave `topic:`/`owner:` sitting in the prose — nothing lost, but a file
        # structurally worse than the well-formed one this merge just consumed. The block
        # is CREATED instead: still an insert, not one existing line rewritten.
        #
        # The header may only be promoted if the reader accepts it whole (`_reader_accepts`).
        # A block the parser rejects makes every bullet in the merged file unreadable, so a
        # header that cannot be metadata travels into the body instead: all of its lines
        # survive, as prose, and the file still parses. It is all-or-nothing deliberately —
        # splitting a header into the parts that "look" keyable is the approximation that
        # let stray lines through twice.
        if _reader_accepts(legacy_meta):
            new_block = legacy_meta
            meta_notes.append(
                f"{_safe(rel_src)}: {_safe(rel_dest)} had no frontmatter — the legacy header became its block"
            )
        else:
            legacy_body = legacy_meta + legacy_body
            demoted_meta = len(legacy_meta)
            meta_notes.append(
                f"{_safe(rel_src)}: its header is not readable as frontmatter, so those lines"
                f" travelled into the body of {_safe(rel_dest)} instead of becoming a block —"
                " every line is there as prose; promote them in review if they were meant"
                " as metadata. Its two `---` delimiters are not carried: they delimited a"
                " block that no longer exists, and a stray pair in a body is read as a"
                " header on the next pass"
            )
    elif canonical_meta is None:
        # An UNTERMINATED block (or a legacy file with no keys to carry). Nothing below the
        # opening delimiter can be proven to be metadata, so there is no block to insert a
        # key into and inventing one would guess at what the file meant: the legacy header
        # travels with the body, verbatim, like every other line the merge cannot place.
        legacy_body = (legacy_meta or []) + legacy_body
    elif legacy_meta:
        carried_meta, leftover, meta_notes = _carry_meta(
            canonical_meta, legacy_meta, rel_src, rel_dest
        )
        # Demoted header lines lead the body from here on. They are counted apart from the
        # verbatim tally below: a demoted `owner:` is a line the merge understood and chose
        # to place, and reporting it as "unparsed" tells a reviewer to go looking for a
        # malformed line that does not exist. `_carry_meta`'s own note already names it.
        demoted_meta = len(leftover)
        legacy_body = leftover + legacy_body
    canonical_bullets = [b for b in (_bullet(l) for l in canonical_body) if b is not None]
    texts = {_normalized(b.text) for b in canonical_bullets}
    keys = {b.topic_key for b in canonical_bullets}
    carried: list[str] = []
    bullets = 0
    verbatim = 0
    divergent = 0
    duplicates: list[str] = []
    for position, line in enumerate(legacy_body):
        if not line.strip():
            continue
        bullet = _bullet(line)
        if bullet is not None:
            text = _normalized(bullet.text)
            if text in texts:
                # The same knowledge, already canonical: canonical wins the sentence, which
                # is Plan 12's collision rule and `fact_text_hash`'s notion of identity.
                # Both files share a stem, so both renderings share a unit id and only the
                # canonical one is in the INDEX (`_dedup` — and see there for the readers
                # that do see both, and why losing this rendering from them is accepted).
                # It can still differ in the columns a HUMAN reads, so the line itself goes
                # into the note rather than a tally: a reviewer who wants this stamp or
                # these tags back can see what they were without reading the diff, up to
                # `_DUPLICATE_LINE_MAX` — past that the note abbreviates and says so.
                duplicates.append(line.strip())
                continue
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
        # Carried without deduplication, deliberately. Deduping non-bullet lines deleted a
        # `---` that closed a block and a fence that closed a code span — each one "a
        # duplicate" by text and structure by role. Bullets have a meaningful identity
        # (their sentence) and are deduped above; for every other line the module's own
        # doctrine settles it: a repeated line costs a line, a deleted one costs knowledge.
        carried.append(line)
        if position >= demoted_meta:
            verbatim += 1
    if carried or carried_meta or new_block:
        _apply_merge(dest, carried_meta, carried, rel_dest, new_block)
    lost = _lost(readable_before, [_readable(dest, _read_text(dest, rel_dest))])
    if lost:
        # The one check that cannot be fooled by reasoning about either file's shape. Put
        # the destination back byte-for-byte and let the caller move the legacy file aside:
        # a merge is a convenience, and keeping every fact readable is not.
        with _guarded(rel_dest, "cannot restore it after refusing the merge"):
            dest.write_bytes(dest_before)
        raise _MergeWouldBury(lost)
    notes = [f"{_safe(rel_src)} merged into {_safe(rel_dest)} ({bullets} bullets)"]
    if divergent:
        notes.append(
            f"{_safe(rel_src)}: {divergent} bullet(s) share a topic key with a canonical bullet"
            " — both kept, reconcile them in review"
        )
    if verbatim:
        notes.append(f"{_safe(rel_src)}: {verbatim} unparsed line(s) carried over verbatim")
    if duplicates:
        # Written out up to a cap, and the cap says where the rest are. This note IS the
        # record: the fold is sound because the index never held these renderings
        # (`_dedup`), but the classify bundle and `mneme verify` did, so a reviewer's only
        # remaining view of a folded `#tag` or `verified:` stamp is this line. Both caps
        # bite hardest in the very shape this is for — a topic file copied and re-verified
        # wholesale, where EVERY bullet folds — so `_DUPLICATES_SHOWN` is the one meant to
        # bind and `_DUPLICATES_BUDGET` is sized from it rather than guessed at. A budget
        # chosen independently silently cut the record to 10 lines for ordinary bullets.
        shown, omitted = _join_capped(
            duplicates, "; ", _DUPLICATES_BUDGET,
            cap=_DUPLICATE_LINE_MAX, limit=_DUPLICATES_SHOWN, elide=True,
        )
        more = (
            f"; and {omitted} more — the full set is in"
            f" {_safe(rel_src)} as of the commit before this migration"
            if omitted
            else ""
        )
        notes.append(
            f"{_safe(rel_src)}: {len(duplicates)} bullet(s) already said what a canonical bullet"
            " says — the canonical line kept. Their own rendering is folded away with them,"
            f" so it is written out here: {shown}{more}"
        )
    notes.extend(meta_notes)
    _remove(repo, src, rel_src)
    return notes


def _apply_merge(
    path: Path, meta: list[str], body: list[str], rel: str, block: list[str] | None = None
) -> None:
    """Insert the carried lines: keys inside the frontmatter block, body after the last
    bullet, and — when the file has no block at all — `block` as a new one at the top.
    Nothing already in the file is rewritten.

    Deliberately the harvest's own line discipline rather than a second implementation of
    it: a merge is a delta edit like any fact apply, so the BOM, the file's dominant line
    ending, and every untouched byte survive exactly as they do there.
    """
    h = _harvest()
    text, bom = h._read_raw(path)
    lines = h._lines_keepends(text)
    contents = [h._split_eol(line)[0] for line in lines]
    end = _frontmatter_end(contents)
    eol = h._dominant_eol(lines)
    at = len(lines)
    for i in range(0 if end is None else end + 1, len(lines)):
        if contents[i].startswith("- ["):
            at = i + 1
    if body:
        if at == 0 and body[0].strip() == "---":
            # Carried body landing at the top of an empty canonical file: a leading `---`
            # would be read as the start of a frontmatter block the rest of the body never
            # closes. In the legacy file these lines were body because a blank line came
            # first; one blank line restores that, and costs a byte no reader looks at.
            lines.insert(0, eol)
            at = 1
        if at == len(lines) and lines:
            # Appending past the end: a file that stopped mid-line is terminated first, so
            # the carried line starts on one of its own. Every interior line already ends
            # in an eol.
            tail, tail_eol = h._split_eol(lines[-1])
            if not tail_eol:
                lines[-1] = tail + eol
        for offset, line in enumerate(body):
            lines.insert(at + offset, line + eol)
    if meta and end is not None:
        # Before the closing delimiter, and after the body insert above, whose index is
        # always the larger of the two.
        for offset, line in enumerate(meta):
            lines.insert(end + offset, line + eol)
    if block and end is None:
        # A block where there was none, last of the three so the earlier indexes are still
        # the ones computed from the file as it was read.
        for offset, line in enumerate(["---", *block, "---"]):
            lines.insert(offset, line + eol)
    # newline="": the line endings in `lines` are the file's own, never retranslated.
    with _guarded(rel, "cannot write the merged file"):
        path.write_text(bom + "".join(lines), encoding="utf-8", newline="")
