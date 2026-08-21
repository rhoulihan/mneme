"""Which mode a registered repo is in, where a user can see it.

Mode changes what `/mneme:classify` will do, where facts land, and whether the repo can be
distributed as a plugin. A user who cannot see it from `status` or `registry list` finds
out when a command refuses — which is the worst moment and the least explanation.
"""
import subprocess

from mneme_core import gitops, registry, scaffold
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def app_repo(tmp_path, home, name="payments-service"):
    repo = tmp_path / name
    repo.mkdir(parents=True)
    (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    gitops.git(repo, "config", "user.email", "t@example.com")
    gitops.git(repo, "config", "user.name", "Test")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "the app")
    registry.add_plugin(home, Plugin(name=name, repo="r", path=str(repo)))
    return repo


def test_status_names_the_mode_of_each_repo(tmp_path, capsys):
    home = tmp_path / "home"
    scaffold.create(home, "acme-knowledge", owner="demo")
    app_repo(tmp_path, home)

    _code, out, _ = run(capsys, "--home", str(home), "status")

    lines = {l.split()[1]: l for l in out.splitlines() if l.startswith("- ")}
    assert "plain" in lines["payments-service"]
    assert "plugin" in lines["acme-knowledge"]
    assert "plain" not in lines["acme-knowledge"]


def test_registry_list_names_the_mode_too(tmp_path, capsys):
    home = tmp_path / "home"
    scaffold.create(home, "acme-knowledge", owner="demo")
    app_repo(tmp_path, home)

    _code, out, _ = run(capsys, "--home", str(home), "registry", "list")

    rows = {l.split()[0]: l for l in out.splitlines() if l.strip()}
    assert "plain" in rows["payments-service"]
    assert "plugin" in rows["acme-knowledge"]


def test_a_repo_whose_clone_is_gone_reports_that_not_a_guess(tmp_path, capsys):
    """`is_plugin` on a missing directory is False, which would read as "plain"."""
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="ghost-kb", repo="r", path=str(tmp_path / "nope")))

    _code, out, _ = run(capsys, "--home", str(home), "status")

    line = next(l for l in out.splitlines() if l.startswith("- ghost-kb"))
    assert "plain" not in line
    assert "no local clone" in line
