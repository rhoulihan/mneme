import os

from mneme_index import build, db


def test_bom_prefixed_fact_file_indexes_first_bullet(tmp_path):
    root = tmp_path / "tree"
    facts = root / "facts"
    facts.mkdir(parents=True)
    (facts / "t.md").write_bytes(
        "﻿- [gotcha] BOM fact #bom (verified: 2026-08-11)\n".encode("utf-8")
    )
    conn = db.open_db(tmp_path / "i.db")
    stats = build.index_tree(conn, "p", root)
    assert stats.facts == 1
    assert stats.skipped == []
    conn.close()


def test_root_stored_resolved(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    (root / "facts").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    conn = db.open_db(tmp_path / "i.db")
    build.index_tree(conn, "p", type(root)("tree"))
    row = conn.execute("SELECT root FROM plugins WHERE name = 'p'").fetchone()
    assert os.path.isabs(row["root"])
    assert row["root"] == str(root.resolve())
    conn.close()
