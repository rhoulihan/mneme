from mneme_core import registry
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_context_with_plugins(tmp_path, capsys):
    home = tmp_path / "home"
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "MNEME.md").write_text(
        "# x\n\n## Scope statement\n\nWidget platform operations.\nMore detail.\n",
        encoding="utf-8",
    )
    registry.add_plugin(
        home, Plugin(name="acme-knowledge", repo="r", path=str(kb), sensitivity="restricted")
    )
    code, out, _ = run(capsys, "--home", str(home), "context")
    assert code == 0
    assert "mneme flag" in out
    assert "- acme-knowledge [restricted]: Widget platform operations." in out
    assert "More detail." not in out


def test_context_without_plugins(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path / "home"), "context")
    assert code == 0
    assert "none — run 'mneme new" in out


def test_context_missing_scope_statement(tmp_path, capsys):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="bare-kb", repo="r", path=str(tmp_path / "nope")))
    code, out, _ = run(capsys, "--home", str(home), "context")
    assert code == 0
    assert "- bare-kb [internal]: (no scope statement)" in out
