"""Index database — derived, rebuildable, never authoritative (spec §6)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mneme_core.errors import MnemeError

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plugins (
  name TEXT PRIMARY KEY,
  root TEXT NOT NULL,
  repo TEXT NOT NULL DEFAULT '',
  mode TEXT NOT NULL DEFAULT '',
  sensitivity TEXT NOT NULL DEFAULT '',
  built_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS units (
  plugin TEXT NOT NULL,
  id TEXT NOT NULL,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
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
  plugin UNINDEXED, id UNINDEXED, name, description, tags
);
"""


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    try:
        conn.executescript(_FTS)
    except sqlite3.OperationalError as e:
        conn.close()
        raise MnemeError(f"this SQLite build lacks FTS5 support: {e}")
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,)
        )
        conn.commit()
    elif row["value"] != SCHEMA_VERSION:
        conn.close()
        raise MnemeError(
            f"index schema version {row['value']} != {SCHEMA_VERSION}; "
            "delete the database and rebuild"
        )
    return conn


def open_db_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise MnemeError(f"index database not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
