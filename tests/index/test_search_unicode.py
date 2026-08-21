"""Regression tests: non-ASCII content must be searchable, not dead weight in the index."""
import pytest

from mneme_core.errors import MnemeError
from mneme_index import build, db, search


def make_tree(root):
    # A knowledge repo is a plugin: the manifest is what tells mneme these `skills/` are
    # its own to lint, rather than an application's directory it must keep its hands off.
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "kb", "version": "0.1.0"}\n', encoding="utf-8"
    )
    d = root / "skills" / "japanese-docs"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\n"
        "name: japanese-docs\n"
        "description: 日本語のドキュメント検索 (search Japanese documentation)\n"
        "---\n"
        "Body\n",
        encoding="utf-8",
    )
    facts = root / "facts"
    facts.mkdir()
    (facts / "cafe.md").write_text(
        "---\n"
        "topic: cafe\n"
        "---\n"
        "- [reference] Café naïve résumé façade menu #café (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "i.db")
    build.index_tree(c, "acme-knowledge", make_tree(tmp_path / "tree"))
    yield c
    c.close()


def test_fts_query_keeps_non_ascii_terms():
    assert search.fts_query("café naïve") == '"café" OR "naïve"'
    assert search.fts_query("日本語のドキュメント検索") == '"日本語のドキュメント検索"'
    # ASCII behaviour from the plan is unchanged.
    assert search.fts_query("deploy widget") == '"deploy" OR "widget"'
    assert search.fts_query('"; DROP TABLE units; --') == '"DROP" OR "TABLE" OR "units"'
    with pytest.raises(MnemeError):
        search.fts_query("!!! ???")


def test_search_finds_accented_content(conn):
    hits = search.search(conn, "café")
    assert any(h["id"].startswith("facts/cafe#") for h in hits)


def test_search_finds_cjk_content(conn):
    hits = search.search(conn, "日本語のドキュメント検索")
    assert any(h["id"] == "skills/japanese-docs" for h in hits)


def test_cjk_query_is_not_rejected_as_unsearchable(conn):
    # Previously raised MnemeError("search query has no searchable terms").
    assert search.search(conn, "検索できない語") == []
