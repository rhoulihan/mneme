import json
import subprocess

import pytest

from mneme_core import lint, paths, registry, scaffold
from mneme_core.errors import MnemeError


def test_create_full_tree(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="acme-team")
    assert target == paths.repos_dir(home) / "acme-knowledge"
    for rel in (
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "MNEME.md",
        "AGENTS.md",
        "README.md",
        "CONTRIBUTING.md",
        "CODEOWNERS",
        ".github/workflows/validate.yml",
        ".github/workflows/release.yml",
        ".gitignore",
        "skills/knowledge-index/SKILL.md",
    ):
        assert (target / rel).exists(), rel
    assert (target / "facts").is_dir()
    data = json.loads((target / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert data["name"] == "acme-knowledge"
    assert data["version"] == "0.1.0"


def test_scaffold_lints_clean(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "clean-knowledge")
    issues = lint.lint_repo(target)
    assert not lint.has_errors(issues)


def test_git_initialized_with_one_commit(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "git-knowledge")
    log = subprocess.run(
        ["git", "-C", str(target), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert len(log.strip().splitlines()) == 1
    assert "scaffold git-knowledge" in log
    branch = subprocess.run(
        ["git", "-C", str(target), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert branch == "main"


def test_registered_in_registry(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "reg-knowledge", sensitivity="restricted", mode="commit")
    p = registry.get_plugin(home, "reg-knowledge")
    assert p is not None
    assert p.path == str(target)
    assert p.sensitivity == "restricted"
    assert p.mode == "commit"
    assert p.repo == f"local:{target}"


def test_existing_target_rejected(tmp_path):
    home = tmp_path / "home"
    scaffold.create(home, "dup-knowledge")
    with pytest.raises(MnemeError):
        scaffold.create(home, "dup-knowledge")


def test_bad_name_rejected(tmp_path):
    with pytest.raises(MnemeError):
        scaffold.create(tmp_path / "home", "Bad_Name")


def test_custom_directory(tmp_path):
    home = tmp_path / "home"
    custom = tmp_path / "elsewhere" / "kb"
    target = scaffold.create(home, "custom-knowledge", directory=custom)
    assert target == custom
    assert (custom / "MNEME.md").exists()
