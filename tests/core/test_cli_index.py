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
    facts = root / "facts"
    facts.mkdir()
    (facts / "staging-env.md").write_text(
        "---\n"
        "topic: staging-env\n"
        "---\n"
        "- [constraint] Staging DB resets nightly at 04:00 UTC #staging (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    return root


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def setup_home(tmp_path):
    home = tmp_path / "home"
    tree = make_tree(tmp_path / "clone")
    registry.add_plugin(home, Plugin(name="acme-knowledge", repo="r", path=str(tree)))
    return home


def test_index_rebuild_and_status(tmp_path, capsys):
    home = setup_home(tmp_path)
    code, out, _ = run(capsys, "--home", str(home), "index", "rebuild")
    assert code == 0
    assert "indexed acme-knowledge: 1 skills, 1 facts, 0 skipped" in out
    code, out, _ = run(capsys, "--home", str(home), "index", "status")
    assert code == 0
    assert "acme-knowledge  skills=1  facts=1" in out
    assert "total_units=2" in out


def test_search_hits(tmp_path, capsys):
    home = setup_home(tmp_path)
    run(capsys, "--home", str(home), "index", "rebuild")
    code, out, _ = run(capsys, "--home", str(home), "search", "nightly")
    assert code == 0
    assert "facts/staging-env#staging-db-resets-nightly-at-04" in out
    code, out, _ = run(capsys, "--home", str(home), "search", "nightly", "--kind", "skill")
    assert code == 0
    assert "staging-env" not in out


def test_search_without_db_is_graceful(tmp_path, capsys):
    home = setup_home(tmp_path)
    code, _, err = run(capsys, "--home", str(home), "search", "nightly")
    assert code == 1
    assert "index not built" in err


def test_db_query_readonly(tmp_path, capsys):
    home = setup_home(tmp_path)
    run(capsys, "--home", str(home), "index", "rebuild")
    code, out, _ = run(capsys, "--home", str(home), "db", "query", "SELECT COUNT(*) FROM units")
    assert code == 0
    assert out.strip() == "2"
    code, _, err = run(
        capsys, "--home", str(home), "db", "query",
        "INSERT INTO meta (key, value) VALUES ('x', 'y')",
    )
    assert code == 1
    assert "mneme:" in err


def test_existing_commands_unaffected(tmp_path, capsys):
    home = setup_home(tmp_path)
    code, out, _ = run(capsys, "--home", str(home), "registry", "list")
    assert code == 0
    assert "acme-knowledge" in out
