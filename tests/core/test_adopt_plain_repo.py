"""Adopting a repo that is not a knowledge repo — an app, a service, an infra repo.

Adoption used to have one shape: write the plugin manifests, claim `skills/`, claim the
root `CONTRIBUTING.md` and `CODEOWNERS`, install repo-wide CI. That is the right thing to
do to a repo whose PURPOSE is shipping knowledge and the wrong thing to do to a payments
service, where every one of those files already belongs to someone else.

So adopt classifies once, says which mode it picked and why, and leaves the repo
unambiguously in that mode afterwards: a plugin has the manifest, a plain repo has
`mneme-index/` and no manifest mneme wrote.
"""
import subprocess

import pytest

from mneme_core import gitops, harvest, lint, registry, scaffold, units
from mneme_core.cli import main
from mneme_core.registry import Plugin

from tests.core.test_plain_repo_harvest import plain_repo, stage_fact


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def app_repo(tmp_path, home, name="payments-service", *, extra=()):
    """An ordinary service repo, registered but not adopted."""
    repo = tmp_path / name
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    for rel, content in extra:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    gitops.git(repo, "config", "user.email", "t@example.com")
    gitops.git(repo, "config", "user.name", "Test")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "the app")
    registry.add_plugin(home, Plugin(name=name, repo="git@example.com:acme/p.git", path=str(repo)))
    return repo


def test_adopting_an_app_does_not_make_it_a_plugin(tmp_path):
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home)

    result = scaffold.adopt(home, "payments-service", owner="pay-team")

    assert result.mode == "plain"
    assert not (repo / ".claude-plugin").exists()
    assert not (repo / ".github" / "workflows" / "release.yml").exists()
    assert not (repo / "skills").exists()


def test_the_plain_knowledge_root_is_seeded_at_the_top_level(tmp_path):
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home)

    result = scaffold.adopt(home, "payments-service")

    assert "MNEME.md" in result.added
    assert "mneme-index/SKILL.md" in result.added
    assert f"{units.FACTS_PLAIN}/.gitkeep" in result.added
    assert (repo / "mneme-index" / "SKILL.md").is_file()
    meta, _ = units.parse_frontmatter((repo / "mneme-index" / "SKILL.md").read_text("utf-8"))
    assert meta["name"] == "mneme-index"


def test_the_app_s_own_root_files_stay_the_app_s(tmp_path):
    """Contribution rules for the knowledge go inside the directory they govern."""
    home = tmp_path / "home"
    repo = app_repo(
        tmp_path, home, extra=[("CONTRIBUTING.md", "# how to work on the payments service\n")]
    )

    result = scaffold.adopt(home, "payments-service")

    assert "CONTRIBUTING.md" not in result.added
    assert (repo / "CONTRIBUTING.md").read_text("utf-8") == "# how to work on the payments service\n"
    assert "mneme-index/CONTRIBUTING.md" in result.added
    text = (repo / "mneme-index" / "CONTRIBUTING.md").read_text("utf-8")
    assert "mneme-index/facts/" in text


def test_ownership_is_claimed_over_the_knowledge_root_not_the_repo(tmp_path):
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home)

    scaffold.adopt(home, "payments-service", owner="pay-team")

    text = (repo / "CODEOWNERS").read_text("utf-8")
    assert "/mneme-index/ @pay-team" in text
    assert "* @pay-team" not in text, "adopting a service must not make mneme own its source"


def test_an_existing_codeowners_is_reported_never_rewritten(tmp_path):
    """Adopt adds files that are missing. It does not edit repo content — it advises."""
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home, extra=[("CODEOWNERS", "* @platform\n")])

    result = scaffold.adopt(home, "payments-service", owner="pay-team")

    assert (repo / "CODEOWNERS").read_text("utf-8") == "* @platform\n"
    assert "CODEOWNERS" not in result.added
    assert any("/mneme-index/ @pay-team" in n for n in result.notes), result.notes


def test_ci_runs_on_the_knowledge_root_and_nothing_else(tmp_path):
    """An app's CI budget is not mneme's to spend on every unrelated pull request."""
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home, extra=[(".github/workflows/validate.yml", "name: theirs\n")])

    result = scaffold.adopt(home, "payments-service")

    # Their own `validate.yml` is neither overwritten nor collided with.
    assert (repo / ".github" / "workflows" / "validate.yml").read_text("utf-8") == "name: theirs\n"
    assert ".github/workflows/mneme-validate.yml" in result.added
    yml = (repo / ".github" / "workflows" / "mneme-validate.yml").read_text("utf-8")

    # EVERY trigger, checked one at a time. Asserting `"paths:" in yml` passes while
    # `pull_request` runs on every change in the repo, because `push` carries a filter of
    # its own — and pull requests are where an app's CI budget actually goes.
    lines = yml.splitlines()
    for trigger in ("  pull_request:", "  push:"):
        assert trigger in lines, yml
        indent = len(trigger) - len(trigger.lstrip())
        block = []
        for line in lines[lines.index(trigger) + 1 :]:
            # A sibling key ENDS the block. Bounding on "still indented at all" let
            # `pull_request:` swallow the whole `push:` stanza below it and pass on that
            # stanza's filter — the mutant that exposed this survived twice.
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                break
            block.append(line)
        assert any(l.strip() == "paths:" for l in block), f"{trigger} is unscoped:\n{yml}"
        assert any('"mneme-index/**"' in l for l in block), f"{trigger} scope:\n{yml}"
    assert "find mneme-index" in yml


def test_a_repo_that_already_carries_skills_is_adopted_as_a_plugin(tmp_path):
    """A hand-built knowledge repo that never grew a manifest is still a knowledge repo.

    Manifest-only classification would file its facts in `mneme-index/` while its curated
    skills sat in `skills/` — split in half, with lint enforcing on neither.
    """
    home = tmp_path / "home"
    repo = app_repo(
        tmp_path, home, name="team-kb",
        extra=[("skills/deploy-widget/SKILL.md",
                "---\nname: deploy-widget\ndescription: Use when deploying the widget\n---\nBody\n")],
    )

    result = scaffold.adopt(home, "team-kb")

    assert result.mode == "plugin"
    assert (repo / ".claude-plugin" / "plugin.json").is_file()
    assert "skills/knowledge-index/SKILL.md" in result.added
    assert not (repo / "mneme-index").exists()


def test_the_classification_can_be_overridden_in_both_directions(tmp_path):
    home = tmp_path / "home"
    app = app_repo(tmp_path, home)
    kb = app_repo(
        tmp_path, home, name="team-kb",
        extra=[("skills/deploy-widget/SKILL.md",
                "---\nname: deploy-widget\ndescription: Use when deploying the widget\n---\nBody\n")],
    )

    assert scaffold.adopt(home, "payments-service", as_plugin=True).mode == "plugin"
    assert (app / ".claude-plugin" / "plugin.json").is_file()

    assert scaffold.adopt(home, "team-kb", as_plugin=False).mode == "plain"
    assert not (kb / ".claude-plugin").exists()
    assert (kb / "mneme-index" / "SKILL.md").is_file()


def test_adopt_says_which_mode_it_picked_and_why(tmp_path, capsys):
    home = tmp_path / "home"
    app_repo(tmp_path, home)
    code, out, _ = run(capsys, "--home", str(home), "adopt", "payments-service")
    assert code == 0
    assert "plain" in out
    assert "mneme-index/" in out


def test_an_adopted_app_lints_clean_and_takes_a_harvest(tmp_path):
    """The whole point: after adoption the ordinary rails work on an ordinary repo."""
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home)
    scaffold.adopt(home, "payments-service")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "chore: adopt mneme")

    assert not lint.has_errors(lint.lint_repo(repo))

    main_before = gitops.git(repo, "rev-parse", "main")
    result = harvest.apply_batch(
        home, "payments-service", [stage_fact(home, "payments-service")], push=False
    )
    assert gitops.git(repo, "rev-parse", "main") == main_before
    tree = gitops.git(repo, "ls-tree", "-r", "--name-only", result.branch).splitlines()
    assert "mneme-index/facts/refunds.md" in tree


def test_the_scope_doc_describes_the_mode_the_repo_is_actually_in(tmp_path):
    """A plain repo has no skills mneme maintains — telling the user to write them is a lie."""
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home)
    scaffold.adopt(home, "payments-service")
    text = (repo / "MNEME.md").read_text("utf-8")
    assert "Durable facts" in text
    assert "skills)" not in text
