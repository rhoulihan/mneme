"""Regression tests: URI-safe paths and schema/corruption guards on the DB layer."""
import sqlite3

import pytest

from mneme_core.errors import MnemeError
from mneme_index import db

UNIT_COLUMNS = (
    "INSERT INTO units (plugin, id, kind, name, description, category, tags,"
    " path, line, verified, hash) VALUES ('p', 'skills/x', 'skill', 'x', 'd',"
    " '', '', 'skills/x/SKILL.md', 0, '', 'h')"
)


@pytest.mark.parametrize("dirname", ["home#1", "home?ro", "home%2Fescape", "home with space"])
def test_uri_special_characters_do_not_redirect_the_connection(tmp_path, dirname):
    """A '#', '?' or '%' in the path must not truncate the SQLite URI.

    Unencoded, SQLite reads the remainder as fragment/query: it opens a *different*
    file and silently drops mode=ro, so reads miss the real data and writes succeed.
    """
    home = tmp_path / dirname
    home.mkdir()
    path = home / "i.db"
    conn = db.open_db(path)
    conn.execute(UNIT_COLUMNS)
    conn.commit()
    conn.close()

    ro = db.open_db_readonly(path)
    try:
        assert ro.execute("SELECT COUNT(*) AS n FROM units").fetchone()["n"] == 1
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("CREATE TABLE sneaky (x)")
        with pytest.raises(sqlite3.OperationalError):
            ro.execute(UNIT_COLUMNS.replace("'skills/x'", "'skills/y'"))
    finally:
        ro.close()

    # No stray database was created at a truncated path.
    assert [p.name for p in tmp_path.iterdir()] == [dirname]
    assert [p.name for p in home.iterdir()] == ["i.db"]


def test_open_db_readonly_rejects_schema_version_mismatch(tmp_path):
    path = tmp_path / "i.db"
    conn = db.open_db(path)
    conn.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(MnemeError) as excinfo:
        db.open_db_readonly(path)
    assert "999" in str(excinfo.value)
    assert "rebuild" in str(excinfo.value)


def test_open_db_readonly_rejects_db_missing_the_fts_table(tmp_path):
    """A same-version DB without units_fts must not surface as `no such table`."""
    path = tmp_path / "i.db"
    db.open_db(path).close()
    conn = sqlite3.connect(str(path))
    conn.execute("DROP TABLE units_fts")
    conn.commit()
    conn.close()
    with pytest.raises(MnemeError) as excinfo:
        db.open_db_readonly(path)
    assert "units_fts" in str(excinfo.value)


def test_open_db_readonly_rejects_foreign_database(tmp_path):
    path = tmp_path / "other.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE unrelated (x)")
    conn.commit()
    conn.close()
    with pytest.raises(MnemeError):
        db.open_db_readonly(path)


def test_open_db_readonly_rejects_empty_file(tmp_path):
    path = tmp_path / "i.db"
    path.write_bytes(b"")
    with pytest.raises(MnemeError):
        db.open_db_readonly(path)


def test_corrupt_database_raises_mneme_error_not_sqlite_error(tmp_path):
    path = tmp_path / "i.db"
    path.write_bytes(b"this is definitely not a sqlite database\n" * 20)
    with pytest.raises(MnemeError):
        db.open_db_readonly(path)
    # The recovery path (open_db, used by rebuild) must degrade the same way.
    with pytest.raises(MnemeError):
        db.open_db(path)
