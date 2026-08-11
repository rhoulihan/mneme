import sqlite3

import pytest

from mneme_core.errors import MnemeError
from mneme_index import db


def test_open_db_creates_schema(tmp_path):
    conn = db.open_db(tmp_path / "i.db")
    names = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }
    assert {"meta", "plugins", "units", "units_fts"} <= names
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    assert row["value"] == db.SCHEMA_VERSION
    conn.close()


def test_open_db_is_idempotent(tmp_path):
    path = tmp_path / "i.db"
    db.open_db(path).close()
    conn = db.open_db(path)
    assert conn.execute("SELECT COUNT(*) AS n FROM meta").fetchone()["n"] == 1
    conn.close()


def test_row_factory_is_row(tmp_path):
    conn = db.open_db(tmp_path / "i.db")
    row = conn.execute("SELECT 1 AS one").fetchone()
    assert row["one"] == 1
    conn.close()


def test_schema_version_mismatch_raises(tmp_path):
    path = tmp_path / "i.db"
    conn = db.open_db(path)
    conn.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(MnemeError):
        db.open_db(path)


def test_open_db_readonly_missing_raises(tmp_path):
    with pytest.raises(MnemeError):
        db.open_db_readonly(tmp_path / "absent.db")


def test_open_db_readonly_rejects_writes(tmp_path):
    path = tmp_path / "i.db"
    db.open_db(path).close()
    conn = db.open_db_readonly(path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO meta (key, value) VALUES ('x', 'y')")
    conn.close()
