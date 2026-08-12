"""Query surface over the index: FTS search, fact filters, status (spec §6.1–6.2)."""
from __future__ import annotations

import re
import sqlite3

from mneme_core.errors import MnemeError


# \w (Unicode) rather than [A-Za-z0-9_]: FTS5's unicode61 tokenizer indexes accented
# and CJK tokens intact, so an ASCII-only extraction would silently truncate "café"
# to "caf" (matching nothing) and reject "日本語" as unsearchable. Punctuation still
# never survives, so raw user text never reaches MATCH unquoted.
_TERM_RE = re.compile(r"\w+", re.UNICODE)


def fts_query(raw: str) -> str:
    terms = _TERM_RE.findall(raw)
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
        "SELECT u.plugin, u.id, u.kind, u.name, u.description, u.summary, u.category, u.tags,"
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


def list_facts(
    conn: sqlite3.Connection,
    category: str | None = None,
    tag: str | None = None,
    plugin: str | None = None,
    topic: str | None = None,
) -> list[dict]:
    sql = (
        "SELECT plugin, id, name, description, category, tags, path, line, verified"
        " FROM units WHERE kind = 'fact'"
    )
    params: list = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    if plugin:
        sql += " AND plugin = ?"
        params.append(plugin)
    if topic:
        sql += " AND name = ?"
        params.append(topic)
    if tag:
        escaped = tag.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql += " AND ' ' || tags || ' ' LIKE ? ESCAPE '\\'"
        params.append(f"% {escaped} %")
    sql += " ORDER BY plugin, path, line"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def status(conn: sqlite3.Connection) -> dict:
    plugins = []
    for p in conn.execute(
        "SELECT name, root, built_at FROM plugins ORDER BY name"
    ).fetchall():
        counts = conn.execute(
            "SELECT"
            " SUM(CASE WHEN kind = 'skill' THEN 1 ELSE 0 END) AS skills,"
            " SUM(CASE WHEN kind = 'fact' THEN 1 ELSE 0 END) AS facts"
            " FROM units WHERE plugin = ?",
            (p["name"],),
        ).fetchone()
        plugins.append(
            {
                "name": p["name"],
                "root": p["root"],
                "built_at": p["built_at"],
                "skills": counts["skills"] or 0,
                "facts": counts["facts"] or 0,
            }
        )
    total = conn.execute("SELECT COUNT(*) AS n FROM units").fetchone()["n"]
    return {"plugins": plugins, "total_units": total}
