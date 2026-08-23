"""Glue between the registry and the standalone mneme_index component (spec §6)."""
from __future__ import annotations

import hashlib
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

    Content costs a read. Measured across the registered repos: 13 files / 28 KB on a slow
    drvfs mount takes ~32ms against ~19ms for `stat` alone — about 12ms for a guarantee,
    on a path a human invokes. Knowledge repos are markdown; this scales with a corpus
    people have to read.

    The path LIST is part of the digest, so a rename registers even though the bytes are
    identical — otherwise the index would go on serving the old topic name forever.
    """
    h = hashlib.sha256()
    paths_to_read = list(units.fact_files(root))
    paths_to_read += [d / "SKILL.md" for d in units.readable_skill_dirs(root)]
    for path in sorted(set(paths_to_read)):
        try:
            data = path.read_bytes()
        except OSError:
            continue  # gone between listing and read: the next build settles it
        h.update(path.relative_to(root).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
        h.update(b"\n")
    return h.hexdigest()


def stale(home: Path) -> list[StaleRepo]:
    """Registered repos the index no longer speaks for, and why.

    Read-only: `mneme search` calls this, and its database connection is deliberately
    read-only behind an authorizer that denies ATTACH and PRAGMA. Rebuilding from a read
    would undo that hardening, put a write on the hot path, and race — there is no locking
    anywhere in `core/`. So this reports and the rebuild stays explicit.
    """
    db_file = paths.db_path(home)
    if not db_file.exists():
        return []
    try:
        conn = index_db.open_db_readonly(db_file)
    except MnemeError:
        return []
    predates = False
    try:
        known = {
            r["name"]: r["fingerprint"]
            for r in conn.execute("SELECT name, fingerprint FROM plugins")
        }
    except sqlite3.OperationalError:
        # A database built before the column existed. `open_db_readonly` cannot migrate it
        # — migrations need a writable connection — so read what IS there and report the
        # truth: these rows are real, they simply cannot be checked. Saying "never indexed"
        # instead would send a user to rebuild a repo that is probably fine, and saying
        # nothing would hide the one thing this function exists to surface.
        predates = True
        try:
            known = {r["name"]: "" for r in conn.execute("SELECT name FROM plugins")}
        except sqlite3.Error:
            known = {}
    finally:
        conn.close()

    out: list[StaleRepo] = []
    for p in registry.load_registry(home):
        root = Path(p.path)
        if not root.is_dir():
            # Never a failure: a repo you cannot read is one the index cannot speak for,
            # and search must still answer with what it has.
            out.append(StaleRepo(p.name, "local clone missing"))
            continue
        if p.name not in known:
            out.append(StaleRepo(p.name, "never indexed"))
            continue
        if predates:
            out.append(StaleRepo(p.name, "indexed before freshness tracking — rebuild once"))
            continue
        if known[p.name] != fingerprint(root):
            out.append(StaleRepo(p.name, "changed since it was indexed"))
    return out


def rebuild(home: Path, *, only_stale: bool = False) -> list[index_build.IndexStats]:
    plugins = registry.load_registry(home)
    if not plugins:
        raise MnemeError("no plugins registered; nothing to index")
    paths.ensure_layout(home)
    if only_stale:
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
            except (MnemeError, OSError) as e:
                # One unindexable plugin must not abort the whole rebuild.
                stats.append(
                    index_build.IndexStats(plugin=p.name, skipped=[f"{p.path}: {e}"])
                )
    finally:
        conn.close()
    return stats
