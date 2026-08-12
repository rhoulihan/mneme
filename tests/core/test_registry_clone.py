import subprocess

from mneme_core import registry
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_source_repo(root):
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "MNEME.md").write_text(
        "# kb\n\n## Scope statement\n\nExisting team knowledge.\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "add", "-A"], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(root),
         "commit", "-m", "seed"], check=True, capture_output=True,
    )
    return root


def test_add_with_clone_clones_and_registers(tmp_path, capsys):
    home = tmp_path / "home"
    src = make_source_repo(tmp_path / "upstream" / "team-kb")
    code, out, _ = run(
        capsys, "--home", str(home), "registry", "add", "team-kb",
        "--repo", str(src), "--clone",
    )
    assert code == 0
    assert "cloned" in out and "registered team-kb" in out
    p = registry.get_plugin(home, "team-kb")
    assert p is not None
    assert (tmp_path / "home" / "repos" / "team-kb" / "MNEME.md").exists()


def test_failed_clone_registers_nothing(tmp_path, capsys):
    home = tmp_path / "home"
    code, _, err = run(
        capsys, "--home", str(home), "registry", "add", "ghost-kb",
        "--repo", str(tmp_path / "does-not-exist"), "--clone",
    )
    assert code == 1
    assert "mneme:" in err
    assert registry.get_plugin(home, "ghost-kb") is None


def test_clone_noop_when_path_exists(tmp_path, capsys):
    home = tmp_path / "home"
    existing = tmp_path / "checkout"
    existing.mkdir()
    code, out, _ = run(
        capsys, "--home", str(home), "registry", "add", "local-kb",
        "--repo", "git@example.com:x.git", "--path", str(existing), "--clone",
    )
    assert code == 0
    assert "already exists" in out
    assert registry.get_plugin(home, "local-kb") is not None
