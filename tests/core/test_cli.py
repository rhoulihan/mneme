import subprocess
import sys
from pathlib import Path

import pytest

from mneme_core import staging
from mneme_core.cli import main
from mneme_core.staging import Candidate, candidate_id

REPO_ROOT = Path(__file__).resolve().parents[2]


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_version(capsys):
    code, out, _ = run(capsys, "--version")
    assert code == 0
    assert out.strip().count(".") == 2


def test_init_and_home(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path), "init")
    assert code == 0
    assert str(tmp_path) in out
    assert (tmp_path / "registry.json").exists()
    code, out, _ = run(capsys, "--home", str(tmp_path), "home")
    assert code == 0
    assert out.strip() == str(tmp_path)


def test_flag_roundtrip(tmp_path, capsys):
    code, out, _ = run(
        capsys, "--home", str(tmp_path), "flag", "learned a thing", "--session", "s1"
    )
    assert code == 0
    assert "flagged" in out
    flags_file = tmp_path / "staging" / "flags.jsonl"
    assert "learned a thing" in flags_file.read_text(encoding="utf-8")


def test_registry_add_list_remove(tmp_path, capsys):
    code, _, _ = run(
        capsys, "--home", str(tmp_path), "registry", "add", "acme-knowledge",
        "--repo", "git@github.com:acme/k.git", "--sensitivity", "restricted",
    )
    assert code == 0
    code, out, _ = run(capsys, "--home", str(tmp_path), "registry", "list")
    assert code == 0
    assert "acme-knowledge" in out
    assert "restricted" in out
    assert "git@github.com:acme/k.git" in out
    code, _, _ = run(capsys, "--home", str(tmp_path), "registry", "remove", "acme-knowledge")
    assert code == 0
    code, out, _ = run(capsys, "--home", str(tmp_path), "registry", "list")
    assert "acme-knowledge" not in out


def test_registry_duplicate_is_error(tmp_path, capsys):
    run(capsys, "--home", str(tmp_path), "registry", "add", "a-b", "--repo", "r")
    code, _, err = run(capsys, "--home", str(tmp_path), "registry", "add", "a-b", "--repo", "r")
    assert code == 1
    assert "mneme:" in err


def test_stage_list(tmp_path, capsys):
    body = "# B\n"
    cand = Candidate(
        id=candidate_id("skill", "acme-knowledge", body),
        type="skill", edit="new", target="acme-knowledge", body=body,
    )
    staging.write_candidate(tmp_path, cand)
    code, out, _ = run(capsys, "--home", str(tmp_path), "stage", "list")
    assert code == 0
    assert cand.id in out


def test_scan_exit_codes(tmp_path, capsys):
    dirty = tmp_path / "dirty.txt"
    dirty.write_text("key = AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")
    code, out, _ = run(capsys, "scan", str(dirty))
    assert code == 2
    assert "aws-access-key" in out
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing secret here\n", encoding="utf-8")
    code, _, _ = run(capsys, "scan", str(clean))
    assert code == 0


def test_lint_exit_codes(tmp_path, capsys):
    # A plugin: `skills/` there is mneme's to enforce against. In a plain repo it is the
    # application's, and lint deliberately says nothing about it.
    (tmp_path / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "kb", "version": "0.1.0"}\n', encoding="utf-8"
    )
    skill = tmp_path / "skills" / "bad-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: wrong-name\ndescription: d\n---\n", encoding="utf-8")
    code, out, _ = run(capsys, "lint", str(tmp_path))
    assert code == 2
    assert "MN003" in out


def test_launcher_end_to_end(tmp_path):
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "mneme"), "--home", str(tmp_path), "init"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert (tmp_path / "registry.json").exists()


def test_missing_file_fails_gracefully(tmp_path, capsys):
    code, _, err = run(capsys, "scan", str(tmp_path / "nope.txt"))
    assert code == 1
    assert "mneme:" in err
    code, _, err = run(capsys, "lint", str(tmp_path / "nope.md"))
    assert code == 1
    assert "mneme:" in err


def test_unreadable_file_does_not_traceback(tmp_path):
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "mneme"), "scan", str(tmp_path / "nope.txt")],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "argv",
    [
        ("frobnicate",),
        ("registry",),
        ("stage",),
        ("--no-such-flag",),
        # PR-only doctrine: --mode is gone from both surfaces, so even a formerly
        # valid value is now an unrecognised argument — and still exit 1, not 2.
        ("registry", "add", "a-b", "--mode", "pr", "--repo", "r"),
        ("new", "a-b", "--mode", "pr"),
    ],
)
def test_usage_errors_exit_1_not_2(capsys, argv):
    # 2 is reserved for findings; a typo'd command must not look like a scan blocker.
    code, _, err = run(capsys, *argv)
    assert code == 1
    assert "mneme:" in err
