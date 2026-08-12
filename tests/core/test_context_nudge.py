import json
import subprocess

from mneme_core import registry
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_kb(tmp_path, name="detected-kb", manifest=True):
    kb = tmp_path / name
    kb.mkdir(parents=True)
    (kb / "MNEME.md").write_text("# scope\n\n## Scope statement\n\nStuff.\n", encoding="utf-8")
    if manifest:
        cp = kb / ".claude-plugin"
        cp.mkdir()
        (cp / "plugin.json").write_text(
            json.dumps({"name": "acme-detected", "version": "0.1.0"}), encoding="utf-8"
        )
    return kb


def test_nudge_for_unregistered_repo(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert code == 0
    assert "Unregistered knowledge repo detected" in out
    assert str(kb.resolve()) in out
    assert "mneme registry add acme-detected" in out
    assert f"local:{kb.resolve()}" in out
    assert "ask the user" in out.lower()
    assert "/mneme:adopt acme-detected" in out


def test_origin_url_used_when_present(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    subprocess.run(["git", "init", "-b", "main", str(kb)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(kb), "remote", "add", "origin", "git@github.com:acme/detected.git"],
        check=True, capture_output=True,
    )
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert "--repo git@github.com:acme/detected.git" in out


def test_no_nudge_when_registered(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    registry.add_plugin(home, Plugin(name="acme-detected", repo="r", path=str(kb)))
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert code == 0
    assert "Unregistered knowledge repo detected" not in out


def test_no_nudge_without_marker_or_cwd(tmp_path, capsys):
    home = tmp_path / "home"
    plain = tmp_path / "plain"
    plain.mkdir()
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(plain))
    assert "Unregistered" not in out
    code, out, _ = run(capsys, "--home", str(home), "context")
    assert code == 0
    assert "Unregistered" not in out


def test_dir_name_fallback_and_slug(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path, name="My Team KB", manifest=False)
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert "mneme registry add my-team-kb" in out
