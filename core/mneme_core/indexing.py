"""Glue between the registry and the standalone mneme_index component (spec §6)."""
from __future__ import annotations

from pathlib import Path

from mneme_index import build as index_build
from mneme_index import db as index_db

from . import paths, registry
from .errors import MnemeError


def rebuild(home: Path) -> list[index_build.IndexStats]:
    plugins = registry.load_registry(home)
    if not plugins:
        raise MnemeError("no plugins registered; nothing to index")
    paths.ensure_layout(home)
    conn = index_db.open_db(paths.db_path(home))
    stats: list[index_build.IndexStats] = []
    try:
        # The index is derived state: a de-registered plugin's rows have no file
        # source left, so they must go or they keep surfacing in every search.
        index_build.prune_plugins(conn, (p.name for p in plugins))
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
                        mode=p.mode,
                        sensitivity=p.sensitivity,
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
