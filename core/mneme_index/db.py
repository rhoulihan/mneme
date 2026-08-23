"""Index database — derived, rebuildable, never authoritative (spec §6)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

from mneme_core.errors import MnemeError

SCHEMA_VERSION = "2"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plugins (
  name TEXT PRIMARY KEY,
  root TEXT NOT NULL,
  repo TEXT NOT NULL DEFAULT '',
  sensitivity TEXT NOT NULL DEFAULT '',
  built_at TEXT NOT NULL DEFAULT '',
  -- What the tree looked like when these rows were built. Empty means indexed before
  -- this column existed, which reads as stale once and then settles.
  fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS units (
  plugin TEXT NOT NULL,
  id TEXT NOT NULL,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL,
  line INTEGER NOT NULL DEFAULT 0,
  verified TEXT NOT NULL DEFAULT '',
  hash TEXT NOT NULL,
  PRIMARY KEY (plugin, id)
);
"""

_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(
  plugin UNINDEXED, id UNINDEXED, name, description, summary, tags
);
"""


def _uri(path: Path, *, readonly: bool) -> str:
    """Build a SQLite URI.

    The path must be percent-encoded: SQLite reads a bare '?' as the start of the
    query parameters and '#' as a fragment, so an unencoded path containing either
    silently opens a *different* file and drops the parameters (including mode=ro).
    """
    mode = "ro" if readonly else "rwc"
    return f"file:{quote(str(path))}?mode={mode}"


def _unusable(path: Path, reason: object) -> MnemeError:
    return MnemeError(
        f"cannot read index database {path}: {reason}; the index is derived state — "
        f"delete {path.name} and rebuild (mneme index rebuild)"
    )


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to the current schema.

    `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so a column
    added later never reaches a database built before it — and the failure is a bare
    `no such column` from whichever query happens to touch it first. Every addition is
    listed here with its default, and adding one twice is a no-op.
    """
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(plugins)")}
    for column, ddl in (("fingerprint", "fingerprint TEXT NOT NULL DEFAULT ''"),):
        if column not in existing:
            conn.execute(f"ALTER TABLE plugins ADD COLUMN {ddl}")
    conn.commit()


def open_db(path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(_uri(path, readonly=False), uri=True)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        _add_missing_columns(conn)
    except sqlite3.Error as e:
        raise _unusable(path, e) from e
    try:
        conn.executescript(_FTS)
    except sqlite3.OperationalError as e:
        conn.close()
        raise MnemeError(f"this SQLite build lacks FTS5 support: {e}")
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,)
            )
            conn.commit()
            return conn
    except sqlite3.Error as e:
        conn.close()
        raise _unusable(path, e) from e
    if row["value"] != SCHEMA_VERSION:
        conn.close()
        raise MnemeError(
            f"index schema version {row['value']} != {SCHEMA_VERSION}; "
            "delete the database and rebuild"
        )
    return conn


_REQUIRED_TABLES = frozenset({"meta", "plugins", "units", "units_fts"})


def open_db_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise MnemeError(f"index database not found: {path}")
    try:
        conn = sqlite3.connect(_uri(path, readonly=True), uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.Error as e:
        raise _unusable(path, e) from e
    if row is None:
        conn.close()
        raise _unusable(path, "no schema_version recorded")
    if row["value"] != SCHEMA_VERSION:
        conn.close()
        raise MnemeError(
            f"index schema version {row['value']} != {SCHEMA_VERSION}; "
            "delete the database and rebuild"
        )
    # A same-version DB missing a table would otherwise surface as a raw
    # "no such table: units_fts" from whichever query reached it first.
    try:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    except sqlite3.Error as e:
        conn.close()
        raise _unusable(path, e) from e
    missing = _REQUIRED_TABLES - tables
    if missing:
        conn.close()
        raise _unusable(path, f"missing table(s) {', '.join(sorted(missing))}")
    return conn
