"""Query surface over the index: FTS search, fact filters, status (spec §6.1–6.2)."""
from __future__ import annotations

import re
import sqlite3

from mneme_core.errors import MnemeError


def fts_query(raw: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_]+", raw)
    if not terms:
        raise MnemeError("search query has no searchable terms")
    return " OR ".join(f'"{t}"' for t in terms)


def search(
    conn: sqlite3.Connection,
    query: str,
    k: int = 10,
    kind: str | None = None,
    plugin: str | None = None,
) -> list[dict]:
    sql = (
        "SELECT u.plugin, u.id, u.kind, u.name, u.description, u.category, u.tags,"
        " u.path, u.line, u.verified, rank AS score"
        " FROM units_fts JOIN units u"
        " ON u.plugin = units_fts.plugin AND u.id = units_fts.id"
        " WHERE units_fts MATCH ?"
    )
    params: list = [fts_query(query)]
    if kind:
        sql += " AND u.kind = ?"
        params.append(kind)
    if plugin:
        sql += " AND u.plugin = ?"
        params.append(plugin)
    sql += " ORDER BY rank LIMIT ?"
    params.append(k)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
