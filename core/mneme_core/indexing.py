"""Glue between the registry and the standalone mneme_index component (spec §6)."""
from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mneme_index import build as index_build
from mneme_index import db as index_db

from . import paths, registry, units
from .errors import MnemeError


@dataclass
class StaleRepo:
    plugin: str
    reason: str


def _unit_files(root: Path) -> tuple[list[Path], list[str]]:
    """(files the index reads, directories it could not list).

    The second value is the whole point. `Path.glob` returns `[]` for a directory it cannot
    read — it swallows the `PermissionError` that `os.scandir` raises — so an unreadable
    facts directory is indistinguishable from an empty one. That blindness is shared by the
    BUILD and by the fingerprint, so the two agree, the rebuild reports zero facts and zero
    skipped as a clean success, and the freshness check compares blind to blind and says
    "fresh" while the repo's knowledge sits on disk.

    Agreement between two observers blinded the same way is not evidence of correctness.
    Every directory is probed explicitly, and what could not be read is returned so callers
    can say so instead of implying an answer they do not have.
    """
    files: list[Path] = []
    problems: list[str] = []

    def probe(d: Path) -> bool:
        try:
            os.scandir(d).close()
            return True
        except OSError as e:
            problems.append(f"{_rel(d, root)}: cannot read ({e.strerror or e})")
            return False

    try:
        fact_dirs = units.facts_dirs(root)
        skill_dirs = units.readable_skill_dirs(root)
    except OSError as e:
        # `is_dir()` on a path under an unreadable parent RAISES, so even listing the
        # candidate directories can fail. `stale()` promises it never does.
        return [], [f"{root.name}: cannot read ({e.strerror or e})"]

    for d in fact_dirs:
        if probe(d):
            files.extend(sorted(d.glob("*.md")))
    for d in skill_dirs:
        if probe(d):
            skill_md = d / "SKILL.md"
            if skill_md.is_file():
                files.append(skill_md)
    return sorted(set(files)), problems


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def fingerprint(root: Path) -> str:
    """A signature of exactly the files the index reads, over their CONTENT.

    NOT `git rev-parse HEAD`. The build reads the WORKING TREE, so an uncommitted edit
    mid-classify, an untracked fact file, or a checkout that leaves HEAD alone all change
    what the index should hold while the sha does not move.

    Not size-and-mtime either, which is the cheaper thing to reach for and is wrong in both
    directions. Measured on this machine, mtime granularity is ~4ms, so an edit landing
    within one tick of the previous write keeps the same timestamp — and "24 hours" ->
    "48 hours" keeps the same size. A fingerprint that can miss an edit silently recreates
    the exact bug it exists to detect. In the other direction, `git checkout` and `git pull`
    rewrite mtimes without touching content, so a timestamp-based signal would call every
    pull a change and burn a rebuild on nothing.

    What could not be READ is hashed in alongside what could, so an unreadable directory
    can never produce the same digest as an empty one.

    Content is read in chunks: `read_bytes` on a pathological file allocated it whole, and
    this runs on `search`, which is the agent's hot path.
    """
    files, problems = _unit_files(root)
    h = hashlib.sha256()
    for problem in problems:
        h.update(b"!unreadable\0")
        h.update(problem.encode("utf-8"))
        h.update(b"\n")
    for path in files:
        h.update(_rel(path, root).encode("utf-8"))
        h.update(b"\0")
        h.update(_content_digest(path))
        h.update(b"\n")
    return h.hexdigest()


def _content_digest(path: Path) -> bytes:
    inner = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                inner.update(chunk)
    except OSError as e:
        # Distinct from both "empty" and "some content": a file that cannot be read is a
        # fact the index does not hold, and the digest has to say so.
        inner.update(f"!unreadable:{e.strerror or e}".encode("utf-8"))
    return inner.digest()


def unreadable_parts(root: Path) -> list[str]:
    """What a caller could not read under `root` — empty when everything was legible."""
    return _unit_files(root)[1]


def stale(home: Path, conn=None) -> list[StaleRepo]:
    """Registered repos the index no longer speaks for, and why. Never raises.

    FAILS CLOSED. Every "cannot tell" — no database, a corrupt one, a schema from the
    future, a concurrent writer holding the lock — is reported as stale, not as fresh.
    Returning `[]` for those meant a fresh install said "index is fresh" with no database
    at all, and a locked one said it while a rebuild was running; the documented
    remediation loop was then unreachable, because nobody rebuilds what is already fine.

    Read-only: `mneme search` calls this, and its connection is deliberately read-only
    behind an authorizer that denies ATTACH and PRAGMA. Rebuilding from a read would undo
    that hardening, put a write on the hot path, and race — there is no locking anywhere
    in `core/`. So this reports and the rebuild stays explicit.
    """
    plugins = registry.load_registry(home)
    db_file = paths.db_path(home)
    # `conn` lets a caller that already has the database open reuse it. `search` opened it
    # twice — once for the query, once here — and a writer taking the lock in that window
    # made the staleness warning vanish while the hits still printed: exit 0, results on
    # stdout, silence on stderr, which is the state this feature exists to make impossible.
    borrowed = conn is not None
    if not borrowed:
        if not db_file.exists():
            return [StaleRepo(p.name, "index not built") for p in plugins]
        try:
            conn = index_db.open_db_readonly(db_file)
        except MnemeError as e:
            return [StaleRepo(p.name, f"index unusable — cannot read it ({e})") for p in plugins]

    predates = False
    unreadable_db = ""
    known: dict[str, str] = {}
    try:
        known = {
            r["name"]: r["fingerprint"]
            for r in conn.execute("SELECT name, fingerprint FROM plugins")
        }
    except sqlite3.OperationalError as e:
        # `OperationalError` is not one condition. "no such column" means a database built
        # before the column existed, which `open_db_readonly` cannot migrate — migrations
        # need a writable connection. "database is locked" means a rebuild is running and
        # we know NOTHING. Treating the second as the first reported a genuinely fresh
        # index as "never indexed", which is the misdiagnosis this whole function exists
        # to avoid making.
        if "no such column" in str(e):
            predates = True
            try:
                known = {r["name"]: "" for r in conn.execute("SELECT name FROM plugins")}
            except sqlite3.Error:
                unreadable_db = str(e)
        else:
            unreadable_db = str(e)
    except sqlite3.Error as e:
        unreadable_db = str(e)
    finally:
        if not borrowed:
            conn.close()

    if unreadable_db:
        return [
            StaleRepo(p.name, f"index could not be read ({unreadable_db})") for p in plugins
        ]

    out: list[StaleRepo] = []
    registered = {p.name for p in plugins}
    for p in plugins:
        root = Path(p.path)
        if not root.is_dir():
            # Never a failure: a repo you cannot read is one the index cannot speak for,
            # and search must still answer with what it has.
            out.append(StaleRepo(p.name, "local clone missing"))
            continue
        if p.name not in known:
            out.append(StaleRepo(p.name, "never indexed"))
            continue
        problems = unreadable_parts(root)
        if problems:
            out.append(StaleRepo(p.name, problems[0]))
            continue
        if predates:
            out.append(StaleRepo(p.name, "indexed before freshness tracking — rebuild once"))
            continue
        if known[p.name] != fingerprint(root):
            out.append(StaleRepo(p.name, "changed since it was indexed"))
    # Rows for a repo nobody registers any more keep answering every search —
    # `prune_plugins` says so in its own docstring — and nothing ever told the user.
    for name in sorted(set(known) - registered):
        out.append(StaleRepo(name, "no longer registered — its rows still answer searches"))
    return out


def rebuild(home: Path, *, only_stale: bool = False) -> list[index_build.IndexStats]:
    plugins = registry.load_registry(home)
    if not plugins and not paths.db_path(home).exists():
        raise MnemeError("no plugins registered; nothing to index")
    paths.ensure_layout(home)
    with paths.locked(home, "index"):
        return _rebuild_locked(home, plugins, only_stale)


def _rebuild_locked(home: Path, plugins, only_stale: bool) -> list[index_build.IndexStats]:
    if only_stale:
        # De-registered names appear in `stale` too, and pruning them is exactly the work
        # `--stale` should do — but they are not in `plugins`, so the filter drops them and
        # `prune_plugins` below does the removing.
        wanted = {s.plugin for s in stale(home)}
        plugins = [p for p in plugins if p.name in wanted]
    conn = index_db.open_db(paths.db_path(home))
    stats: list[index_build.IndexStats] = []
    try:
        # The index is derived state: a de-registered plugin's rows have no file
        # source left, so they must go or they keep surfacing in every search.
        # Pruning is a whole-registry judgement, so it must see the whole registry — a
        # `--stale` pass narrows what gets REBUILT, never what counts as still registered.
        index_build.prune_plugins(conn, (p.name for p in registry.load_registry(home)))
        for p in plugins:
            root = Path(p.path)
            if not root.is_dir():
                stats.append(
                    index_build.IndexStats(
                        plugin=p.name, skipped=[f"{p.path}: local clone missing"]
                    )
                )
                continue
            # What could not be READ, reported before the build runs. `index_tree` sees
            # an unreadable directory as an empty one — `Path.glob` swallows the error —
            # so without this a rebuild that indexed nothing reports "0 skipped", which
            # reads as a clean success and is how an entire repo left the index in silence.
            blind = unreadable_parts(root)
            try:
                stats.append(
                    index_build.index_tree(
                        conn,
                        p.name,
                        root,
                        repo=p.repo,
                        sensitivity=p.sensitivity,
                        fingerprint=fingerprint(root),
                    )
                )
                stats[-1].skipped.extend(blind)
            except (MnemeError, OSError) as e:
                # One unindexable plugin must not abort the whole rebuild.
                stats.append(
                    index_build.IndexStats(plugin=p.name, skipped=[f"{p.path}: {e}"])
                )
    finally:
        conn.close()
    return stats
