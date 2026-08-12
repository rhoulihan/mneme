import json
from pathlib import Path

from mneme_core import registry
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_new_creates_and_registers(tmp_path, capsys):
    home = tmp_path / "home"
    code, out, _ = run(capsys, "--home", str(home), "new", "acme-knowledge", "--owner", "acme")
    assert code == 0
    assert "created" in out and "registered acme-knowledge" in out
    assert registry.get_plugin(home, "acme-knowledge") is not None


def test_new_then_lint_and_index(tmp_path, capsys):
    home = tmp_path / "home"
    run(capsys, "--home", str(home), "new", "flow-knowledge")
    p = registry.get_plugin(home, "flow-knowledge")
    code, _, _ = run(capsys, "lint", p.path)
    assert code == 0
    code, out, _ = run(capsys, "--home", str(home), "index", "rebuild")
    assert code == 0
    assert "indexed flow-knowledge: 1 skills, 0 facts" in out


def test_new_duplicate_errors(tmp_path, capsys):
    home = tmp_path / "home"
    run(capsys, "--home", str(home), "new", "dup-knowledge")
    code, _, err = run(capsys, "--home", str(home), "new", "dup-knowledge")
    assert code == 1
    assert "mneme:" in err


def test_new_with_quoted_description_writes_valid_manifest(tmp_path, capsys):
    home = tmp_path / "home"
    hostile = 'He said "hello" to the DB'
    code, _, _ = run(
        capsys, "--home", str(home), "new", "quote-knowledge", "--description", hostile
    )
    assert code == 0
    p = registry.get_plugin(home, "quote-knowledge")
    manifest = Path(p.path) / ".claude-plugin" / "plugin.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["description"] == hostile


def test_new_with_long_description_succeeds(tmp_path, capsys):
    home = tmp_path / "home"
    code, _, err = run(
        capsys, "--home", str(home), "new", "long-knowledge", "--description", "x" * 950
    )
    assert code == 0, err
    assert registry.get_plugin(home, "long-knowledge") is not None


def test_new_custom_dir(tmp_path, capsys):
    home = tmp_path / "home"
    custom = tmp_path / "kb"
    code, out, _ = run(
        capsys, "--home", str(home), "new", "dir-knowledge", "--dir", str(custom)
    )
    assert code == 0
    assert str(custom) in out
