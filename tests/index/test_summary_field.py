from mneme_index import build, db, search


def make_tree(root):
    d = root / "skills" / "deploy-widget"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: deploy-widget\ndescription: Use when deploying the widget service\n---\n"
        "Run the preflight checklist, then execute the blue-green cutover procedure.\n",
        encoding="utf-8",
    )
    facts = root / "facts"
    facts.mkdir()
    (facts / "t.md").write_text(
        "---\ntopic: t\n---\n- [gotcha] Plain fact #x (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    return root


def test_schema_version_is_2():
    assert db.SCHEMA_VERSION == "2"


def test_skill_summary_extracted_and_searchable(tmp_path):
    conn = db.open_db(tmp_path / "i.db")
    build.index_tree(conn, "p", make_tree(tmp_path / "tree"))
    row = conn.execute("SELECT summary FROM units WHERE kind = 'skill'").fetchone()
    assert "blue-green cutover" in row["summary"]
    hits = search.search(conn, "cutover preflight")
    assert any(h["id"] == "skills/deploy-widget" for h in hits)
    assert "summary" in hits[0]
    conn.close()


def test_fact_summary_empty(tmp_path):
    conn = db.open_db(tmp_path / "i.db")
    build.index_tree(conn, "p", make_tree(tmp_path / "tree"))
    row = conn.execute("SELECT summary FROM units WHERE kind = 'fact'").fetchone()
    assert row["summary"] == ""
    conn.close()


def test_summary_capped_at_400(tmp_path):
    root = tmp_path / "tree"
    d = root / "skills" / "long-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: long-skill\ndescription: d\n---\n" + ("word " * 300),
        encoding="utf-8",
    )
    conn = db.open_db(tmp_path / "i.db")
    build.index_tree(conn, "p", root)
    row = conn.execute("SELECT summary FROM units WHERE kind = 'skill'").fetchone()
    assert len(row["summary"]) <= 400
    conn.close()
