"""Regression: template constants written raw must not carry string.Template escapes.

The scaffolded validate.yml shipped with literal $$f/$$rc because the constant
was escaped for string.Template but never rendered through it — bash then
expanded $$ as the shell PID, feeding garbage filenames to the secret scan and
crashing `exit $rc` (first observed as a CI failure on mneme-dev-knowledge).
"""
from pathlib import Path

from mneme_core import scaffold, templates


def test_validate_yml_has_no_template_escapes():
    assert "$$" not in templates.VALIDATE_YML
    assert '"$f"' in templates.VALIDATE_YML
    assert "rc=$?" in templates.VALIDATE_YML
    assert "exit $rc" in templates.VALIDATE_YML


def test_no_written_scaffold_file_carries_template_escapes(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "varfix-knowledge", owner="demo")
    for path in target.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "$$" not in text, str(path.relative_to(target))
