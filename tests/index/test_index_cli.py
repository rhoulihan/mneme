import json
import subprocess
import sys
from pathlib import Path

from mneme_index.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_build_and_search(tmp_path, capsys):
    tree = make_tree(tmp_path / "acme-tree")
    dbfile = str(tmp_path / "i.db")
    code, out, _ = run(capsys, "--db", dbfile, "build", str(tree))
    assert code == 0
    assert "indexed acme-tree: 1 skills, 1 facts, 0 skipped" in out

    code, out, _ = run(capsys, "--db", dbfile, "search", "nightly")
    assert code == 0
    assert "facts/staging-env#staging-db-resets-nightly-at-04" in out

    code, out, _ = run(capsys, "--db", dbfile, "search", "nightly", "--json")
    assert code == 0
    hits = json.loads(out)
    assert hits[0]["plugin"] == "acme-tree"


def test_build_with_name(tmp_path, capsys):
    tree = make_tree(tmp_path / "t")
    code, out, _ = run(capsys, "--db", str(tmp_path / "i.db"), "build", str(tree), "--name", "custom-label")
    assert code == 0
    assert "indexed custom-label:" in out


def test_facts_and_status(tmp_path, capsys):
    tree = make_tree(tmp_path / "t")
    dbfile = str(tmp_path / "i.db")
    run(capsys, "--db", dbfile, "build", str(tree), "--name", "p")
    code, out, _ = run(capsys, "--db", dbfile, "facts", "--category", "constraint")
    assert code == 0
    assert "[constraint]" in out
    code, out, _ = run(capsys, "--db", dbfile, "status")
    assert code == 0
    assert "p  skills=1  facts=1" in out
    assert "total_units=2" in out


def test_search_missing_db_is_graceful(tmp_path, capsys):
    code, _, err = run(capsys, "--db", str(tmp_path / "absent.db"), "search", "x")
    assert code == 1
    assert "mneme-index:" in err


def test_launcher_end_to_end(tmp_path):
    tree = make_tree(tmp_path / "t")
    dbfile = str(tmp_path / "i.db")
    launcher = str(REPO_ROOT / "bin" / "mneme-index")
    r1 = subprocess.run(
        [sys.executable, launcher, "--db", dbfile, "build", str(tree)],
        capture_output=True, text=True,
    )
    assert r1.returncode == 0
    r2 = subprocess.run(
        [sys.executable, launcher, "--db", dbfile, "search", "widget"],
        capture_output=True, text=True,
    )
    assert r2.returncode == 0
    assert "skills/deploy-widget" in r2.stdout
