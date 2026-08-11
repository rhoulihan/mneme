import pytest

from mneme_index import build, db, search


@pytest.fixture
def conn(tmp_path):
    root = tmp_path / "tree"
    facts = root / "facts"
    facts.mkdir(parents=True)
    (facts / "t.md").write_text(
        "---\ntopic: t\n---\n"
        "- [gotcha] Underscore tagged fact #api_v2 (verified: 2026-08-11)\n"
        "- [gotcha] Plain tagged fact #apiXv2 (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    c = db.open_db(tmp_path / "i.db")
    build.index_tree(c, "p", root)
    yield c
    c.close()


def test_underscore_is_literal_not_wildcard(conn):
    rows = search.list_facts(conn, tag="api_v2")
    assert len(rows) == 1
    assert "Underscore" in rows[0]["description"]


def test_percent_matches_nothing_literal(conn):
    assert search.list_facts(conn, tag="api%") == []
