import pytest

from mneme_index import build, db, search


def make_tree(root):
    # A knowledge repo is a plugin: the manifest is what tells mneme these `skills/` are
    # its own to lint, rather than an application's directory it must keep its hands off.
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "kb", "version": "0.1.0"}\n', encoding="utf-8"
    )
    facts = root / "facts"
    facts.mkdir(parents=True)
    (facts / "staging-env.md").write_text(
        "---\n"
        "topic: staging-env\n"
        "---\n"
        "- [constraint] Staging DB resets nightly at 04:00 UTC #staging (verified: 2026-08-11)\n"
        "- [gotcha] v2 API truncates batch writes over 500 items #api (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    d = root / "skills" / "deploy-widget"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: deploy-widget\ndescription: Use when deploying the widget service\n---\nBody\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "i.db")
    build.index_tree(c, "acme-knowledge", make_tree(tmp_path / "tree"))
    yield c
    c.close()


def test_list_all_facts(conn):
    rows = search.list_facts(conn)
    assert len(rows) == 2
    assert [r["line"] for r in rows] == [4, 5]
    assert set(rows[0]) == {
        "plugin", "id", "name", "description", "category", "tags", "path", "line", "verified",
    }


def test_category_filter(conn):
    rows = search.list_facts(conn, category="gotcha")
    assert len(rows) == 1
    assert rows[0]["category"] == "gotcha"


def test_tag_filter_exact_token(conn):
    assert len(search.list_facts(conn, tag="api")) == 1
    assert search.list_facts(conn, tag="ap") == []


def test_topic_and_plugin_filters(conn):
    assert len(search.list_facts(conn, topic="staging-env")) == 2
    assert search.list_facts(conn, plugin="nope") == []


def test_status(conn):
    st = search.status(conn)
    assert st["total_units"] == 3
    assert len(st["plugins"]) == 1
    p = st["plugins"][0]
    assert p["name"] == "acme-knowledge"
    assert p["skills"] == 1
    assert p["facts"] == 2
    assert p["built_at"].endswith("+00:00")


def test_status_empty_db(tmp_path):
    c = db.open_db(tmp_path / "empty.db")
    st = search.status(c)
    assert st == {"plugins": [], "total_units": 0}
    c.close()
