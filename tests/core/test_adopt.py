from pathlib import Path

from mneme_core import registry, scaffold
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_existing_plugin(tmp_path, home):
    repo = tmp_path / "existing-kb"
    d = repo / "skills" / "legacy-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: legacy-skill\ndescription: A hand-written team skill\n---\nBody\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# existing\n", encoding="utf-8")
    registry.add_plugin(
        home,
        Plugin(name="existing-kb", repo="git@example.com:kb.git", path=str(repo),
               sensitivity="restricted", mode="pr"),
    )
    return repo


def test_adopt_adds_only_missing(tmp_path, capsys):
    home = tmp_path / "home"
    repo = make_existing_plugin(tmp_path, home)
    added = scaffold.adopt(home, "existing-kb", owner="team-leads")
    assert "MNEME.md" in added
    assert ".claude-plugin/plugin.json" in added
    assert "skills/knowledge-index/SKILL.md" in added
    # never overwrites
    assert (repo / "README.md").read_text(encoding="utf-8") == "# existing\n"
    # registry sensitivity flows into MNEME.md
    text = (repo / "MNEME.md").read_text(encoding="utf-8")
    assert "restricted" in text
    assert "* @team-leads" in (repo / "CODEOWNERS").read_text(encoding="utf-8")


def test_adopt_is_idempotent(tmp_path, capsys):
    home = tmp_path / "home"
    make_existing_plugin(tmp_path, home)
    scaffold.adopt(home, "existing-kb")
    assert scaffold.adopt(home, "existing-kb") == []


def test_adopt_never_touches_existing_mneme_md(tmp_path, capsys):
    home = tmp_path / "home"
    repo = make_existing_plugin(tmp_path, home)
    (repo / "MNEME.md").write_text("# custom scope\n", encoding="utf-8")
    added = scaffold.adopt(home, "existing-kb")
    assert "MNEME.md" not in added
    assert (repo / "MNEME.md").read_text(encoding="utf-8") == "# custom scope\n"


def test_adopt_cli_reports(tmp_path, capsys):
    home = tmp_path / "home"
    make_existing_plugin(tmp_path, home)
    code, out, _ = run(capsys, "--home", str(home), "adopt", "existing-kb", "--owner", "x-team")
    assert code == 0
    assert "added: MNEME.md" in out
    code, out, _ = run(capsys, "--home", str(home), "adopt", "existing-kb")
    assert "nothing to add" in out


def test_adopt_unknown_or_missing_clone(tmp_path, capsys):
    home = tmp_path / "home"
    code, _, err = run(capsys, "--home", str(home), "adopt", "ghost")
    assert code == 1
    registry.add_plugin(home, Plugin(name="gone-kb", repo="r", path=str(tmp_path / "nope")))
    code, _, err = run(capsys, "--home", str(home), "adopt", "gone-kb")
    assert code == 1


def test_adopt_warns_on_legacy_lint_errors(tmp_path, capsys):
    home = tmp_path / "home"
    repo = make_existing_plugin(tmp_path, home)
    bad = repo / "skills" / "broken-skill"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: Wrong_Name\n---\n", encoding="utf-8")
    code, out, _ = run(capsys, "--home", str(home), "adopt", "existing-kb")
    assert code == 0
    assert "warning:" in out and "lint error" in out
