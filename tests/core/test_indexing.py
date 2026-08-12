import pytest

from mneme_core import indexing, paths, registry
from mneme_core.errors import MnemeError
from mneme_core.registry import Plugin
from mneme_index import db as index_db


def make_tree(root):
    d = root / "skills" / "deploy-widget"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: deploy-widget\ndescription: Use when deploying the widget service\n---\nBody\n",
        encoding="utf-8",
    )
    return root


def test_rebuild_indexes_registered_plugins(tmp_path):
    home = tmp_path / "home"
    tree = make_tree(tmp_path / "clone")
    registry.add_plugin(
        home,
        Plugin(
            name="acme-knowledge",
            repo="git@github.com:acme/k.git",
            path=str(tree),
            sensitivity="internal",
        ),
    )
    stats = indexing.rebuild(home)
    assert len(stats) == 1
    assert stats[0].skills == 1
    conn = index_db.open_db_readonly(paths.db_path(home))
    row = conn.execute("SELECT * FROM plugins WHERE name = 'acme-knowledge'").fetchone()
    assert row["repo"] == "git@github.com:acme/k.git"
    assert row["sensitivity"] == "internal"
    # PR-only doctrine: the index carries no contribution mode column.
    assert "mode" not in row.keys()
    conn.close()


def test_missing_clone_is_skipped_not_fatal(tmp_path):
    home = tmp_path / "home"
    tree = make_tree(tmp_path / "clone")
    registry.add_plugin(home, Plugin(name="good-plugin", repo="r", path=str(tree)))
    registry.add_plugin(
        home, Plugin(name="gone-plugin", repo="r", path=str(tmp_path / "absent"))
    )
    stats = indexing.rebuild(home)
    by_name = {s.plugin: s for s in stats}
    assert by_name["good-plugin"].skills == 1
    assert by_name["gone-plugin"].skills == 0
    assert any("local clone missing" in s for s in by_name["gone-plugin"].skipped)


def test_empty_registry_raises(tmp_path):
    with pytest.raises(MnemeError):
        indexing.rebuild(tmp_path / "home")
