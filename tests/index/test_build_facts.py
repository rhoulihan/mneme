import pytest

from mneme_index import build, db


def make_tree(root):
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
    yield c
    c.close()


def test_index_facts(conn, tmp_path):
    tree = make_tree(tmp_path / "tree")
    stats = build.index_tree(conn, "p", tree)
    assert stats.facts == 2
    rows = conn.execute(
        "SELECT * FROM units WHERE kind = 'fact' ORDER BY line"
    ).fetchall()
    first = rows[0]
    assert first["id"] == "facts/staging-env#staging-db-resets-nightly-at-04"
    assert first["name"] == "staging-env"
    assert first["category"] == "constraint"
    assert first["tags"] == "staging"
    assert first["line"] == 4
    assert first["verified"] == "2026-08-11"
    assert first["path"] == "facts/staging-env.md"
    assert rows[1]["category"] == "gotcha"
    assert rows[1]["line"] == 5


def test_malformed_bullet_skipped_rest_indexed(conn, tmp_path):
    root = tmp_path / "tree"
    facts = root / "facts"
    facts.mkdir(parents=True)
    (facts / "t.md").write_text(
        "---\ntopic: t\n---\n"
        "- [broken no close\n"
        "- [gotcha] good bullet (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    stats = build.index_tree(conn, "p", root)
    assert stats.facts == 1
    assert len(stats.skipped) == 1
    assert "t.md:4" in stats.skipped[0]


def test_duplicate_topic_key_deduped(conn, tmp_path):
    root = tmp_path / "tree"
    facts = root / "facts"
    facts.mkdir(parents=True)
    (facts / "t.md").write_text(
        "---\ntopic: t\n---\n"
        "- [gotcha] Alpha beta gamma delta epsilon zeta one #x (verified: 2026-08-11)\n"
        "- [gotcha] Alpha beta gamma delta epsilon zeta two #y (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    stats = build.index_tree(conn, "p", root)
    assert stats.facts == 1
    assert any("duplicate unit id" in s for s in stats.skipped)


def test_topic_falls_back_to_stem(conn, tmp_path):
    root = tmp_path / "tree"
    facts = root / "facts"
    facts.mkdir(parents=True)
    (facts / "notopic.md").write_text(
        "- [reference] See the runbook #ops (verified: 2026-08-11)\n", encoding="utf-8"
    )
    build.index_tree(conn, "p", root)
    row = conn.execute("SELECT * FROM units WHERE kind = 'fact'").fetchone()
    assert row["name"] == "notopic"
    assert row["line"] == 1
