from mneme_core import registry
from mneme_core.cli import main
from mneme_core.registry import Plugin


def make_tree(root):
    d = root / "skills" / "deploy-widget"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: deploy-widget\ndescription: Use when deploying the widget service\n---\nBody\n",
        encoding="utf-8",
    )
    return root


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def setup_indexed_home(tmp_path, capsys):
    home = tmp_path / "home"
    tree = make_tree(tmp_path / "clone")
    registry.add_plugin(home, Plugin(name="acme-knowledge", repo="r", path=str(tree)))
    run(capsys, "--home", str(home), "index", "rebuild")
    return home


def test_select_still_works(tmp_path, capsys):
    home = setup_indexed_home(tmp_path, capsys)
    code, out, _ = run(capsys, "--home", str(home), "db", "query", "SELECT COUNT(*) FROM units")
    assert code == 0
    assert out.strip() == "1"


def test_attach_rejected_and_no_file_created(tmp_path, capsys):
    home = setup_indexed_home(tmp_path, capsys)
    evil = tmp_path / "evil.db"
    code, _, err = run(
        capsys, "--home", str(home), "db", "query", f"ATTACH DATABASE '{evil}' AS evil"
    )
    assert code == 1
    assert "only SELECT queries are allowed" in err
    assert not evil.exists()


def test_leading_whitespace_and_case_allowed(tmp_path, capsys):
    home = setup_indexed_home(tmp_path, capsys)
    code, out, _ = run(capsys, "--home", str(home), "db", "query", "  select 1")
    assert code == 0
    assert out.strip() == "1"


def test_multi_statement_rejected(tmp_path, capsys):
    home = setup_indexed_home(tmp_path, capsys)
    code, _, err = run(
        capsys, "--home", str(home), "db", "query", "SELECT 1; DELETE FROM units"
    )
    assert code == 1
    assert "mneme:" in err


def test_non_select_rejected(tmp_path, capsys):
    home = setup_indexed_home(tmp_path, capsys)
    code, _, err = run(capsys, "--home", str(home), "db", "query", "PRAGMA user_version = 9")
    assert code == 1
    assert "only SELECT queries are allowed" in err
