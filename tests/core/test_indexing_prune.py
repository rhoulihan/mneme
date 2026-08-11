"""Regression tests: rebuild drops de-registered plugins and survives one bad plugin."""
from mneme_core import indexing, paths, registry
from mneme_core.registry import Plugin
from mneme_index import db as index_db
from mneme_index import search as index_search


def make_tree(root, skill_name):
    d = root / "skills" / skill_name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: Use when handling {skill_name}\n---\nBody\n",
        encoding="utf-8",
    )
    facts = root / "facts"
    facts.mkdir()
    (facts / "notes.md").write_text(
        "---\n"
        "topic: notes\n"
        f"---\n- [reference] Everything about {skill_name} #note (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    return root


def test_rebuild_drops_deregistered_plugin(tmp_path):
    home = tmp_path / "home"
    # Distinct, non-overlapping vocabulary so a hit can only come from one tree.
    old_tree = make_tree(tmp_path / "old", "zephyr")
    new_tree = make_tree(tmp_path / "new", "quasar")
    registry.add_plugin(home, Plugin(name="old-plugin", repo="r", path=str(old_tree)))
    indexing.rebuild(home)

    registry.remove_plugin(home, "old-plugin")
    registry.add_plugin(home, Plugin(name="new-plugin", repo="r", path=str(new_tree)))
    indexing.rebuild(home)

    conn = index_db.open_db_readonly(paths.db_path(home))
    try:
        st = index_search.status(conn)
        assert [p["name"] for p in st["plugins"]] == ["new-plugin"]
        assert st["total_units"] == 2
        assert index_search.search(conn, "zephyr") == []
        assert index_search.search(conn, "quasar")
    finally:
        conn.close()


def test_rebuild_keeps_registered_plugins(tmp_path):
    home = tmp_path / "home"
    tree = make_tree(tmp_path / "clone", "keep-widget")
    registry.add_plugin(home, Plugin(name="keep-plugin", repo="r", path=str(tree)))
    indexing.rebuild(home)
    stats = indexing.rebuild(home)
    assert [s.plugin for s in stats] == ["keep-plugin"]
    conn = index_db.open_db_readonly(paths.db_path(home))
    try:
        assert index_search.status(conn)["total_units"] == 2
    finally:
        conn.close()


def test_one_unreadable_plugin_does_not_abort_the_rebuild(tmp_path):
    home = tmp_path / "home"
    bad_tree = make_tree(tmp_path / "bad", "bad-widget")
    (bad_tree / "facts" / "binary.md").write_bytes(
        b"---\ntopic: b\n---\n- [gotcha] \xff\xfe #bad\n"
    )
    good_tree = make_tree(tmp_path / "good", "good-widget")
    # "a-" / "z-" prefixes keep the bad plugin ahead of the good one in registry order.
    registry.add_plugin(home, Plugin(name="a-bad-plugin", repo="r", path=str(bad_tree)))
    registry.add_plugin(home, Plugin(name="z-good-plugin", repo="r", path=str(good_tree)))

    by_name = {s.plugin: s for s in indexing.rebuild(home)}
    assert set(by_name) == {"a-bad-plugin", "z-good-plugin"}
    assert by_name["z-good-plugin"].skills == 1
    assert by_name["z-good-plugin"].facts == 1
    assert any("binary.md" in s for s in by_name["a-bad-plugin"].skipped)


def test_index_rebuild_cli_reports_corrupt_db_gracefully(tmp_path, capsys):
    from mneme_core.cli import main

    home = tmp_path / "home"
    tree = make_tree(tmp_path / "clone", "widget")
    registry.add_plugin(home, Plugin(name="acme", repo="r", path=str(tree)))
    paths.ensure_layout(home)
    paths.db_path(home).write_bytes(b"not a sqlite database at all\n" * 20)

    code = main(["--home", str(home), "index", "rebuild"])
    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("mneme: ")
    assert "rebuild" in err


def test_search_cli_reports_corrupt_db_gracefully(tmp_path, capsys):
    from mneme_core.cli import main

    home = tmp_path / "home"
    tree = make_tree(tmp_path / "clone", "widget")
    registry.add_plugin(home, Plugin(name="acme", repo="r", path=str(tree)))
    paths.ensure_layout(home)
    paths.db_path(home).write_bytes(b"not a sqlite database at all\n" * 20)

    code = main(["--home", str(home), "search", "widget"])
    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("mneme: ")
