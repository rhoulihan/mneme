"""Regression tests: bad content lands in `skipped`; builds never crash (plan Task 2/3)."""
import pytest

from mneme_index import build, db


def make_tree(root):
    # A knowledge repo is a plugin: the manifest is what tells mneme these `skills/` are
    # its own to lint, rather than an application's directory it must keep its hands off.
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "kb", "version": "0.1.0"}\n', encoding="utf-8"
    )
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


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "i.db")
    yield c
    c.close()


def test_non_utf8_fact_file_is_skipped_not_fatal(conn, tmp_path):
    root = make_tree(tmp_path / "tree")
    (root / "facts" / "binary.md").write_bytes(
        b"---\ntopic: binary\n---\n- [gotcha] \xff\xfe not utf-8 #bad\n"
    )
    stats = build.index_tree(conn, "p", root)
    assert stats.skills == 1
    assert stats.facts == 1
    assert any("binary.md" in s and "UTF-8" in s for s in stats.skipped)


def test_non_utf8_skill_file_is_skipped_not_fatal(conn, tmp_path):
    root = make_tree(tmp_path / "tree")
    bad = root / "skills" / "binary-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_bytes(b"---\nname: binary-skill\ndescription: \xff\xfe\n---\n")
    stats = build.index_tree(conn, "p", root)
    assert stats.skills == 1
    assert any("binary-skill" in s and "UTF-8" in s for s in stats.skipped)


def test_directory_named_skill_md_is_skipped_not_fatal(conn, tmp_path):
    root = make_tree(tmp_path / "tree")
    (root / "skills" / "weird" / "SKILL.md").mkdir(parents=True)
    stats = build.index_tree(conn, "p", root)
    assert stats.skills == 1
    assert stats.facts == 1
    assert any("weird" in s for s in stats.skipped)


def test_directory_named_like_a_fact_file_is_skipped_not_fatal(conn, tmp_path):
    root = make_tree(tmp_path / "tree")
    (root / "facts" / "weird.md").mkdir()
    stats = build.index_tree(conn, "p", root)
    assert stats.facts == 1
    assert any("weird.md" in s for s in stats.skipped)


def test_cli_reports_bad_content_instead_of_crashing(tmp_path, capsys):
    from mneme_index.cli import main

    root = make_tree(tmp_path / "acme-tree")
    (root / "facts" / "binary.md").write_bytes(b"---\ntopic: b\n---\n- [gotcha] \xff\xfe #bad\n")
    code = main(["--db", str(tmp_path / "i.db"), "build", str(root)])
    out = capsys.readouterr().out
    assert code == 0
    assert "indexed acme-tree: 1 skills, 1 facts, 1 skipped" in out
    assert "skipped: facts/binary.md" in out
