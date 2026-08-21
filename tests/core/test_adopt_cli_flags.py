"""The `--as-plugin` / `--plain` tri-state, exercised through the CLI a user actually types.

Every existing test called `scaffold.adopt(..., as_plugin=...)` in Python, so the argparse
wiring, the `args.as_plugin` plumbing and the reported reason were all unasserted. A
one-character edit — `--plain` declared `store_true` instead of `store_false` — passed the
entire suite while doing this:

    $ mneme adopt team-kb --plain --owner pay-team
    mode: plugin — requested with --as-plugin
    added: ... CODEOWNERS ... release.yml ... .claude-plugin/plugin.json

`--plain` annexing a repo is precisely the failure this whole feature exists to prevent.
"""
import subprocess

import pytest

from mneme_core import gitops, registry, units
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def app_repo(tmp_path, home, name="payments-service", extra=()):
    repo = tmp_path / name
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    for rel, content in extra:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    gitops.git(repo, "config", "user.email", "t@example.com")
    gitops.git(repo, "config", "user.name", "Test")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "the app")
    registry.add_plugin(home, Plugin(name=name, repo="r", path=str(repo)))
    return repo


SKILL = ("skills/deploy-widget/SKILL.md",
         "---\nname: deploy-widget\ndescription: Use when deploying the widget\n---\nBody\n")


def test_plain_on_the_command_line_keeps_the_repo_plain(tmp_path, capsys):
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home, extra=[SKILL])

    code, out, _ = run(capsys, "--home", str(home), "adopt", "payments-service", "--plain")

    assert code == 0
    assert "mode: plain — requested with --plain" in out
    assert not (repo / ".claude-plugin").exists()
    assert not (repo / ".github" / "workflows" / "release.yml").exists()
    assert (repo / units.PLAIN_ROOT / "SKILL.md").is_file()
    assert "* @" not in (repo / "CODEOWNERS").read_text("utf-8")


def test_as_plugin_on_the_command_line_makes_it_a_plugin(tmp_path, capsys):
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home, extra=[SKILL])

    code, out, _ = run(capsys, "--home", str(home), "adopt", "payments-service", "--as-plugin")

    assert code == 0
    assert "mode: plugin — requested with --as-plugin" in out
    assert (repo / ".claude-plugin" / "plugin.json").is_file()
    assert (repo / units.PLUGIN_ROOT / "SKILL.md").is_file()
    assert not (repo / units.PLAIN_ROOT).exists()


def test_with_no_flag_an_application_is_left_alone(tmp_path, capsys):
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home, extra=[SKILL])

    code, out, _ = run(capsys, "--home", str(home), "adopt", "payments-service")

    assert code == 0
    assert "mode: plain" in out
    assert "--as-plugin" in out, "the ambiguity must be reported, not silently resolved"
    assert not (repo / ".claude-plugin").exists()


def test_the_last_flag_wins(tmp_path, capsys):
    home = tmp_path / "home"
    app_repo(tmp_path, home)
    code, out, _ = run(
        capsys, "--home", str(home), "adopt", "payments-service", "--as-plugin", "--plain"
    )
    assert code == 0
    assert "requested with --plain" in out


def test_describe_reports_the_mode_the_flag_asks_for(tmp_path, capsys):
    """`--describe` hard-coded `_adopt_mode(target, None)`, so the bundle contradicted the
    flag the user had just typed — and the agent drafts a scope against that bundle."""
    import json

    home = tmp_path / "home"
    app_repo(tmp_path, home, extra=[SKILL])

    _c, out, _ = run(capsys, "--home", str(home), "adopt", "payments-service", "--describe")
    assert json.loads(out)["repo"]["mode"] == "plain"

    _c, out, _ = run(
        capsys, "--home", str(home), "adopt", "payments-service", "--describe", "--as-plugin"
    )
    bundle = json.loads(out)
    assert bundle["repo"]["mode"] == "plugin"
    assert bundle["repo"]["knowledge_root"] == units.PLUGIN_ROOT
