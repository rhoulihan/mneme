from pathlib import Path

from mneme_core import registry, routing
from mneme_core.registry import Plugin

MNEME_MD = """# acme-knowledge — knowledge scope

**Sensitivity:** internal

## Scope statement

Widget platform operations: deploys, the staging environment, and the v2 API.

## What belongs here

- stuff
"""


def make_plugin_dir(root: Path, text: str = MNEME_MD) -> Path:
    root.mkdir(parents=True)
    (root / "MNEME.md").write_text(text, encoding="utf-8")
    return root


def test_read_scope_statement_extracts_section(tmp_path):
    d = make_plugin_dir(tmp_path / "kb")
    statement = routing.read_scope_statement(d / "MNEME.md")
    assert statement == "Widget platform operations: deploys, the staging environment, and the v2 API."


def test_read_scope_statement_missing_file_or_heading(tmp_path):
    assert routing.read_scope_statement(tmp_path / "absent" / "MNEME.md") == ""
    p = tmp_path / "noheading.md"
    p.write_text("# nothing here\n", encoding="utf-8")
    assert routing.read_scope_statement(p) == ""


def test_scopes_lists_registered_plugins_sorted(tmp_path):
    home = tmp_path / "home"
    b = make_plugin_dir(tmp_path / "b-kb")
    a = make_plugin_dir(tmp_path / "a-kb")
    registry.add_plugin(home, Plugin(name="b-plugin", repo="r", path=str(b), sensitivity="restricted"))
    registry.add_plugin(home, Plugin(name="a-plugin", repo="r", path=str(a)))
    result = routing.scopes(home)
    assert [s.name for s in result] == ["a-plugin", "b-plugin"]
    assert result[0].statement.startswith("Widget platform operations")
    assert result[1].sensitivity == "restricted"
    assert result[0].mode == "pr"


def test_scopes_tolerates_missing_clone(tmp_path):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="gone-plugin", repo="r", path=str(tmp_path / "nope")))
    result = routing.scopes(home)
    assert result[0].statement == ""
