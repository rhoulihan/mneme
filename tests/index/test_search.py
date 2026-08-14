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
    d = root / "skills" / "deploy-widget"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\n"
        "name: deploy-widget\n"
        "description: Use when deploying the widget service\n"
        "metadata:\n"
        "  mneme-last-verified: 2026-08-11\n"
        "keywords:\n"
        "  - deploy\n"
        "  - widget\n"
        "---\n"
        "Body\n",
        encoding="utf-8",
    )
    facts = root / "facts"
    facts.mkdir()
    (facts / "staging-env.md").write_text(
        "---\n"
        "topic: staging-env\n"
        "---\n"
        "- [constraint] Staging DB resets nightly at 04:00 UTC #staging (verified: 2026-08-11)\n"
        "- [gotcha] v2 API truncates batch writes over 500 items #api (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "i.db")
    build.index_tree(c, "acme-knowledge", make_tree(tmp_path / "tree"))
    yield c
    c.close()


def test_fts_query_sanitizes():
    assert search.fts_query("deploy widget") == '"deploy" OR "widget"'
    assert search.fts_query('"; DROP TABLE units; --') == '"DROP" OR "TABLE" OR "units"'
    with pytest.raises(MnemeError):
        search.fts_query("!!! ???")


def test_search_finds_fact_by_vague_words(conn):
    hits = search.search(conn, "database resets nightly")
    assert any(h["id"] == "facts/staging-env#staging-db-resets-nightly-at-04" for h in hits)
    hit = hits[0]
    assert set(hit) == {
        "plugin", "id", "kind", "name", "description", "category",
        "tags", "path", "line", "verified", "score", "summary",
    }


def test_search_finds_skill(conn):
    hits = search.search(conn, "deploying")
    assert any(h["id"] == "skills/deploy-widget" for h in hits)


def test_kind_filter(conn):
    hits = search.search(conn, "widget staging nightly", kind="skill")
    assert hits
    assert all(h["kind"] == "skill" for h in hits)


def test_plugin_filter(conn, tmp_path):
    other = tmp_path / "other-tree"
    facts = other / "facts"
    facts.mkdir(parents=True)
    (facts / "misc.md").write_text(
        "---\ntopic: misc\n---\n"
        "- [reference] Widget history archive #widget (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    build.index_tree(conn, "other-plugin", other)
    hits = search.search(conn, "widget", plugin="other-plugin")
    assert hits
    assert all(h["plugin"] == "other-plugin" for h in hits)


def test_k_limits_results(conn):
    hits = search.search(conn, "widget staging nightly batch", k=1)
    assert len(hits) == 1


def test_no_match_returns_empty(conn):
    assert search.search(conn, "zzqx") == []
