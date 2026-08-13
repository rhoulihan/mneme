from pathlib import Path

from mneme_core import registry, scaffold, units
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_existing_plugin(tmp_path, home, name="existing-kb"):
    repo = tmp_path / name
    d = repo / "skills" / "legacy-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: legacy-skill\ndescription: A hand-written team skill\n---\nBody\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# existing\n", encoding="utf-8")
    registry.add_plugin(
        home,
        Plugin(name=name, repo="git@example.com:kb.git", path=str(repo),
               sensitivity="restricted"),
    )
    return repo


def test_adopt_adds_only_missing(tmp_path, capsys):
    home = tmp_path / "home"
    repo = make_existing_plugin(tmp_path, home)
    added = scaffold.adopt(home, "existing-kb", owner="team-leads")
    assert "MNEME.md" in added
    assert ".claude-plugin/plugin.json" in added
    assert "skills/knowledge-index/SKILL.md" in added
    assert f"{units.FACTS_CANONICAL}/.gitkeep" in added
    assert not (repo / "facts").exists()
    # never overwrites
    assert (repo / "README.md").read_text(encoding="utf-8") == "# existing\n"
    # registry sensitivity flows into MNEME.md
    text = (repo / "MNEME.md").read_text(encoding="utf-8")
    assert "restricted" in text
    # PR-only doctrine: adopt writes no contribution mode into the scope doc.
    assert "Contribution mode" not in text
    assert "* @team-leads" in (repo / "CODEOWNERS").read_text(encoding="utf-8")


def legacy_facts(repo):
    """A top-level facts file, exactly as a pre-0.5 repo carries it."""
    (repo / "facts").mkdir()
    path = repo / "facts" / "billing.md"
    path.write_text(
        "---\ntopic: billing\n---\n"
        "- [decision] Invoices settle monthly #billing (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    return path


def test_adopt_seeds_canonical_beside_an_existing_legacy_facts_dir(tmp_path, capsys):
    """Adoption seeds the canonical dir even here, and still moves nothing itself.

    Plan 10 skipped the canonical directory whenever a top-level `facts/` existed, which is
    the accommodation the 2026-08-12 directive retires: writes are canonical, and a legacy
    layout is migrated by the next contribution rather than preserved by adoption. What
    adopt must NOT do is any of the migrating — it never rewrites, moves or deletes repo
    content, so the legacy file is byte-identical afterwards and still the file every
    reader resolves to.
    """
    home = tmp_path / "home"
    repo = make_existing_plugin(tmp_path, home)
    fact = legacy_facts(repo)
    before = fact.read_bytes()

    added = scaffold.adopt(home, "existing-kb")

    assert f"{units.FACTS_CANONICAL}/.gitkeep" in added
    assert (repo / units.FACTS_CANONICAL / ".gitkeep").is_file()
    # Nothing was moved, rewritten or removed: the legacy dir holds exactly what it held.
    assert fact.read_bytes() == before
    assert [p.name for p in sorted((repo / "facts").iterdir())] == ["billing.md"]
    # ...and nothing new was seeded at the top level either.
    assert [rel for rel in added if rel.startswith("facts/")] == []
    # The seeded canonical dir is empty, so it cannot shadow the facts that are still
    # legacy: every reader keeps resolving to the real file until the migration runs.
    assert units.fact_files(repo) == [fact]
    index = (repo / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    assert "| billing | facts/billing.md | 1 |" in index


def test_adopt_cli_reports_the_pending_migration(tmp_path, capsys):
    """The user is told what will happen to the legacy dir — and is not told when there is
    no legacy dir to happen to."""
    home = tmp_path / "home"
    repo = make_existing_plugin(tmp_path, home, name="legacy-kb")
    legacy_facts(repo)
    code, out, _ = run(capsys, "--home", str(home), "adopt", "legacy-kb")
    assert code == 0
    notice = [line for line in out.splitlines() if line.startswith("legacy facts layout:")]
    assert len(notice) == 1, out
    # Where it is, where it goes, when, and the command for "I want it now".
    assert "facts/" in notice[0]
    assert "skills/knowledge-index/facts" in notice[0]
    assert "next contribution" in notice[0]
    assert "mneme migrate" in notice[0]

    make_existing_plugin(tmp_path, home, name="canonical-kb")
    code, out, _ = run(capsys, "--home", str(home), "adopt", "canonical-kb")
    assert code == 0
    assert "legacy facts layout" not in out


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
