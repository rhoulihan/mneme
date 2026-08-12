import pytest

from mneme_core.errors import MnemeError
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


def test_index_skills(conn, tmp_path):
    tree = make_tree(tmp_path / "tree")
    stats = build.index_tree(conn, "acme-knowledge", tree)
    assert stats.plugin == "acme-knowledge"
    assert stats.skills == 1
    row = conn.execute(
        "SELECT * FROM units WHERE kind = 'skill' AND plugin = 'acme-knowledge'"
    ).fetchone()
    assert row["id"] == "skills/deploy-widget"
    assert row["name"] == "deploy-widget"
    assert row["description"] == "Use when deploying the widget service"
    assert row["tags"] == "deploy widget"
    assert row["verified"] == "2026-08-11"
    assert row["path"] == "skills/deploy-widget/SKILL.md"
    assert row["line"] == 0
    assert len(row["hash"]) == 12
    fts = conn.execute(
        "SELECT COUNT(*) AS n FROM units_fts WHERE plugin = 'acme-knowledge'"
        " AND id = 'skills/deploy-widget'"
    ).fetchone()
    assert fts["n"] == 1


def test_plugins_row_upserted(conn, tmp_path):
    tree = make_tree(tmp_path / "tree")
    build.index_tree(conn, "acme-knowledge", tree, repo="git@x:y.git", sensitivity="internal")
    p = conn.execute("SELECT * FROM plugins WHERE name = 'acme-knowledge'").fetchone()
    assert p["repo"] == "git@x:y.git"
    assert p["sensitivity"] == "internal"
    assert p["built_at"].endswith("+00:00")
    # PR-only doctrine: the plugins row has no contribution-mode column.
    assert "mode" not in p.keys()


def test_skipped_entries(conn, tmp_path):
    tree = tmp_path / "tree"
    (tree / "skills" / "no-skill-md").mkdir(parents=True)
    (tree / "skills" / "bad-frontmatter").mkdir()
    (tree / "skills" / "bad-frontmatter" / "SKILL.md").write_text(
        "---\nname: x\nno closing delim", encoding="utf-8"
    )
    (tree / "skills" / "no-description").mkdir()
    (tree / "skills" / "no-description" / "SKILL.md").write_text(
        "---\nname: no-description\n---\n", encoding="utf-8"
    )
    stats = build.index_tree(conn, "p", tree)
    assert stats.skills == 0
    assert len(stats.skipped) == 3
    assert any("no-skill-md" in s for s in stats.skipped)


def test_reindex_replaces_rows(conn, tmp_path):
    tree = make_tree(tmp_path / "tree")
    build.index_tree(conn, "p", tree)
    skill_md = tree / "skills" / "deploy-widget" / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8").replace(
            "Use when deploying the widget service", "Updated description"
        ),
        encoding="utf-8",
    )
    build.index_tree(conn, "p", tree)
    rows = conn.execute("SELECT * FROM units WHERE kind = 'skill' AND plugin = 'p'").fetchall()
    assert len(rows) == 1
    assert rows[0]["description"] == "Updated description"
    fts_n = conn.execute("SELECT COUNT(*) AS n FROM units_fts WHERE plugin = 'p'").fetchone()["n"]
    units_n = conn.execute("SELECT COUNT(*) AS n FROM units WHERE plugin = 'p'").fetchone()["n"]
    assert fts_n == units_n


def test_missing_root_raises(conn, tmp_path):
    with pytest.raises(MnemeError):
        build.index_tree(conn, "p", tmp_path / "absent")


def test_tree_without_skills_dir_is_fine(conn, tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    stats = build.index_tree(conn, "p", root)
    assert stats.skills == 0
    assert stats.skipped == []
