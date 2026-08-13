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
        raise MnemeError(f"cannot migrate {rel}: {what} — {e.strerror or e}") from e


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
    _migrate_into(repo, legacy, canonical, LEGACY_DIRNAME, units.FACTS_CANONICAL, result)
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
        dest = _contained(canonical, name, rel_src)
        if name == ".gitkeep":
            # A placeholder is not knowledge: the canonical directory it was standing in
            # for is about to exist for real.
            _remove(repo, src, rel_src)
            continue
        if _occupied(dest):
            if name.endswith(".md") and src.is_file() and dest.is_file():
                try:
                    result.merged.extend(_merge(repo, src, dest, rel_src, rel_dest))
                except _MergeWouldBury as refused:
                    # The destination cannot hold these facts readably — its own header is
                    # broken, so anything folded into it stops being retrievable. The file
                    # keeps its knowledge and its history under a free name beside it.
                    aside, rel_aside = _aside(canonical, name, rel_canonical, rel_src)
                    _move(repo, src, aside, rel_src, rel_aside)
                    result.moved.append(
                        f"{rel_src} -> {rel_aside} (kept separate: merging into {rel_dest}"
                        f" would have made {len(refused.lost)} readable item(s) unreadable,"
                        f" because {rel_dest} does not parse — fix its frontmatter and merge"
                        " the two by hand. Note the saved file's unit ids move with its"
                        f" name, from facts/{name[:-3]}#… to facts/{rel_aside.rsplit('/', 1)[-1][:-3]}#…)"
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
                f"cannot migrate {rel_src}: {rel_dest} already exists —"
                " move or merge it by hand, then run the migration again"
            )
        with _guarded(rel_src, f"cannot create {rel_canonical}/"):
            dest.parent.mkdir(parents=True, exist_ok=True)
        _move(repo, src, dest, rel_src, rel_dest)
        result.moved.append(f"{rel_src} -> {rel_dest}")


def _aside(canonical: Path, name: str, rel_canonical: str, rel_src: str) -> tuple[Path, str]:
    """A free name beside `name` in the canonical directory, for a file that cannot merge.

    `<stem>.legacy.md`, then `-2`, `-3`… Every candidate goes through `_contained`, because
    the stem still comes from a legacy filename; the suffix is appended to a name that
    proof has already accepted, so it cannot introduce a segment of its own.
    """
    stem = name[: -len(".md")] if name.endswith(".md") else name
    for attempt in range(1, 100):
        suffix = ".legacy" if attempt == 1 else f".legacy-{attempt}"
        candidate = f"{stem}{suffix}.md"
        path = _contained(canonical, candidate, rel_src)
        if not _occupied(path):
            return path, f"{rel_canonical}/{candidate}"
    raise MnemeError(
        f"cannot migrate {rel_src}: {rel_canonical}/{stem}.legacy*.md are all taken —"
        " reconcile them by hand, then run the migration again"
    )


def _drop_empty(legacy: Path, rel: str) -> None:
    """Remove a legacy directory the migration emptied; refuse while anything remains.

    `git rm` prunes a directory it emptied, so the directory may already be gone.
    """
    if not legacy.is_dir():
        return
    remaining = sorted(p.name for p in legacy.iterdir())
    if remaining:
        raise MnemeError(
            f"{rel}/ still holds {', '.join(remaining)} after migration —"
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
    walked = repo
    for part in Path(units.FACTS_CANONICAL).parts:
        walked = walked / part
        if walked.is_symlink():
            rel = walked.relative_to(repo).as_posix()
            raise MnemeError(
                f"{rel} is a symlink, not a directory — refusing to migrate into"
                f" {units.FACTS_CANONICAL}/ through it: every fact would be renamed to the"
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
            f"cannot migrate {rel_src}: it is a symlink, and the file it points at is not"
            " mneme's to move or delete — replace it with the file itself (or remove it),"
            " then run the migration again"
        )


def _contained(canonical: Path, name: str, rel_src: str) -> Path:
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
    direction is the one thing it may not do. `harvest._body_start` raises there instead,
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


def _retrievable(text: str) -> set[str]:
    """Everything a READER can actually get out of `text`: fact sentences AND metadata keys.

    Not what the bytes contain — what `units.parse_frontmatter` plus the bullet grammar
    yield, because that pair is what lint, the index build, search and the classify bundle
    all walk. A file whose header the parser rejects yields nothing at all, however much is
    sitting in it, which is why this set is the unit of measurement: knowledge lost is
    knowledge that stopped being retrievable.

    Metadata counts. Measuring only bullets left a legacy file that parses and carries
    `topic:`/`owner:`/`sources:` but no parseable bullet free to be folded into a canonical
    file the reader rejects and then deleted, with success reported — the same failure this
    check exists to stop, one field type over. `_carry_meta` has always said as much: an
    `owner:` key "is a line a human committed exactly as much as a bullet is".

    Keys, not key/value pairs, deliberately: when both files set the same key the canonical
    value wins the header and the legacy block travels into the body with both values named
    in the report. That is a documented reconciliation, not a disappearance — the key is
    still retrievable. A key that yields NOTHING afterwards is the loss this measures.
    """
    try:
        meta, body = units.parse_frontmatter(text)
    except MnemeError:
        return set()
    found = {f"meta:{key}" for key in meta}
    for line in _line_contents(body):
        bullet = _bullet(line)
        if bullet is not None:
            found.add(f"fact:{_normalized(bullet.text)}")
    return found


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
    one whose value differs is *reported* rather than silently resolved, because both
    values are knowledge and only a human can pick. Anything the frontmatter grammar cannot
    key travels with the body instead of into the block, so a stray line in a legacy header
    can never make the canonical header unparseable.
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
                differing.append(f"{have[key]} (kept) vs {legacy_value} (from {rel_src})")
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
            f"{rel_src}: its header is not readable as frontmatter alongside {rel_dest}'s,"
            " so those lines travelled into the body — nothing was dropped; promote them"
            " in review if they were meant as metadata"
        )
    elif keys:
        notes.append(f"{rel_src}: frontmatter key(s) carried over: {', '.join(keys)}")
    if differing:
        notes.append(
            f"{rel_src}: frontmatter differs from {rel_dest} — {'; '.join(differing)}"
            " — reconcile in review"
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

    def __init__(self, lost: set[str]):
        super().__init__("the merge would bury retrievable facts")
        self.lost = lost


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
    dest_text = _read_text(dest, rel_dest)
    src_text = _read_text(src, rel_src)
    # Measured on the way in, checked on the way out: the merge is allowed to move a fact
    # anywhere, and not allowed to make one stop being readable.
    retrievable_before = _retrievable(dest_text) | _retrievable(src_text)
    canonical_meta, canonical_body = _sections(dest_text)
    legacy_meta, legacy_body = _sections(src_text)
    carried_meta: list[str] = []
    new_block: list[str] = []
    meta_notes: list[str] = []
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
                f"{rel_src}: {rel_dest} had no frontmatter — the legacy header became its block"
            )
        else:
            legacy_body = legacy_meta + legacy_body
            meta_notes.append(
                f"{rel_src}: its header is not readable as frontmatter, so those lines"
                f" travelled into the body of {rel_dest} instead of becoming a block —"
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
        legacy_body = leftover + legacy_body
    canonical_bullets = [b for b in (_bullet(l) for l in canonical_body) if b is not None]
    texts = {_normalized(b.text) for b in canonical_bullets}
    keys = {b.topic_key for b in canonical_bullets}
    carried: list[str] = []
    bullets = 0
    verbatim = 0
    divergent = 0
    duplicates = 0
    for line in legacy_body:
        if not line.strip():
            continue
        bullet = _bullet(line)
        if bullet is not None:
            text = _normalized(bullet.text)
            if text in texts:
                # The same knowledge, already canonical: canonical wins the sentence. Its
                # category, tags and verified stamp go with it though, so the count is
                # reported — a reviewer who wants the legacy line's `#tags` back needs to
                # know one was folded away, not just that the file grew by nothing.
                duplicates += 1
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
        verbatim += 1
    if carried or carried_meta or new_block:
        _apply_merge(dest, carried_meta, carried, rel_dest, new_block)
    lost = retrievable_before - _retrievable(_read_text(dest, rel_dest))
    if lost:
        # The one check that cannot be fooled by reasoning about either file's shape. Put
        # the destination back byte-for-byte and let the caller move the legacy file aside:
        # a merge is a convenience, and keeping every fact readable is not.
        with _guarded(rel_dest, "cannot restore it after refusing the merge"):
            dest.write_bytes(dest_before)
        raise _MergeWouldBury(lost)
    notes = [f"{rel_src} merged into {rel_dest} ({bullets} bullets)"]
    if divergent:
        notes.append(
            f"{rel_src}: {divergent} bullet(s) share a topic key with a canonical bullet"
            " — both kept, reconcile them in review"
        )
    if verbatim:
        notes.append(f"{rel_src}: {verbatim} unparsed line(s) carried over verbatim")
    if duplicates:
        notes.append(
            f"{rel_src}: {duplicates} bullet(s) already said what a canonical bullet says"
            " — the canonical line kept, so their tags, category and verified stamp were"
            " folded away with them; the diff has them if you want one back"
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
