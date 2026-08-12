from mneme_core import paths, registry
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_enable_with_empty_registry_creates_empty_db(tmp_path, capsys):
    home = tmp_path / "home"
    code, out, _ = run(capsys, "--home", str(home), "db", "enable")
    assert code == 0
    assert "index enabled" in out
    assert paths.db_path(home).exists()


def test_enable_with_registry_populates(tmp_path, capsys):
    home = tmp_path / "home"
    tree = tmp_path / "clone"
    d = tree / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: a-skill\ndescription: d\n---\nBody\n", encoding="utf-8"
    )
    registry.add_plugin(home, Plugin(name="p-one", repo="r", path=str(tree)))
    code, out, _ = run(capsys, "--home", str(home), "db", "enable")
    assert code == 0
    assert "indexed p-one: 1 skills" in out
    code, out, _ = run(capsys, "--home", str(home), "search", "d")
    assert code == 0


def test_disable_removes_db_and_is_idempotent(tmp_path, capsys):
    home = tmp_path / "home"
    run(capsys, "--home", str(home), "db", "enable")
    assert paths.db_path(home).exists()
    code, out, _ = run(capsys, "--home", str(home), "db", "disable")
    assert code == 0
    assert "index disabled" in out
    assert not paths.db_path(home).exists()
    code, _, _ = run(capsys, "--home", str(home), "db", "disable")
    assert code == 0
