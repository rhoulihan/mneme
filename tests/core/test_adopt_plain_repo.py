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

from mneme_core import gitops, harvest, lint, registry, scaffold, templates, units
from mneme_core.errors import MnemeError
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


@pytest.mark.parametrize("where", ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"])
def test_an_existing_codeowners_is_reported_never_rewritten(tmp_path, where):
    """Adopt adds files that are missing. It does not edit repo content — it advises.

    All THREE locations GitHub reads, not just the root one. Only the root case was covered,
    and it passed for the wrong reason: the write loop's own `exists()` skip masked it. With
    the repo's file at `.github/CODEOWNERS`, adopt created a second one at the root AND
    reported that it hadn't — and a second CODEOWNERS changes which file GitHub honours for
    the whole repo, so mneme silently re-routed code review in somebody else's service.
    """
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home, extra=[(where, "* @platform\n")])

    result = scaffold.adopt(home, "payments-service", owner="pay-team")

    assert (repo / where).read_text("utf-8") == "* @platform\n"
    assert "CODEOWNERS" not in result.added
    # And no NEW one appeared anywhere — the assertion the advice-only check never made.
    found = [p for p in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
             if (repo / p).is_file()]
    assert found == [where], found
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
    # `BELONGS_PLUGIN` also contains "Durable facts:", so that alone is satisfied by the
    # wrong branch; and pinning the stray ")" from "procedures (skills):" breaks on any
    # rewording. Assert the two constants directly — they are the thing being chosen between.
    assert templates.BELONGS_PLAIN.splitlines()[0] in text
    assert templates.BELONGS_PLUGIN.splitlines()[0] not in text


def test_plain_is_refused_on_a_repo_that_carries_a_manifest(tmp_path):
    """Mode must be TRUE of the repo afterwards, not just claimed by a flag.

    `--plain` here reported `mneme-index/SKILL.md` and then wrote
    `skills/knowledge-index/SKILL.md` — the one directory `--plain` promises never to claim
    — because `regenerate_index_skill` re-derived the mode from the manifest. Even fixed,
    the combination is incoherent: the manifest is what makes a repo a plugin, every later
    read and write resolves through it, and removing it is repo content mneme does not edit.
    """
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home)
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "payments", "version": "0.1.0"}\n', encoding="utf-8"
    )

    with pytest.raises(MnemeError, match="plugin.json"):
        scaffold.adopt(home, "payments-service", as_plugin=False)

    assert not (repo / "mneme-index").exists()
    assert not (repo / "skills").exists()


def test_a_repo_carrying_skills_is_no_longer_annexed_on_a_guess(tmp_path):
    """`skills/` is what a repo using Claude Code has. It is not consent to be a plugin.

    Classifying on it wrote plugin manifests, a root CODEOWNERS routing EVERY pull request
    in the repo, repo-wide CI, and a `release.yml` that commits and pushes to `main` on its
    own — into an application, on a guess, from one file the repo already had.
    """
    home = tmp_path / "home"
    repo = app_repo(
        tmp_path, home,
        extra=[("skills/deploy-widget/SKILL.md",
                "---\nname: deploy-widget\ndescription: Use when deploying the widget\n---\nBody\n")],
    )

    result = scaffold.adopt(home, "payments-service")

    assert result.mode == "plain"
    assert not (repo / ".claude-plugin").exists()
    assert not (repo / ".github" / "workflows" / "release.yml").exists()
    assert (repo / "CODEOWNERS").read_text("utf-8").count("* @") == 0
    # But the ambiguity is REPORTED, so a real knowledge repo is one flag away.
    assert any("--as-plugin" in n and "skills/" in n for n in result.notes), result.notes


def test_plugin_mode_is_as_careful_with_codeowners_as_plain_mode(tmp_path):
    """`_plain_files` checked three locations for an existing CODEOWNERS; the plugin path
    checked none, so adopting wrote `* @maintainers` beside a repo's own `.github/CODEOWNERS`."""
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home, extra=[(".github/CODEOWNERS", "* @platform-team\n")])

    result = scaffold.adopt(home, "payments-service", as_plugin=True)

    assert "CODEOWNERS" not in result.added
    assert not (repo / "CODEOWNERS").exists()
    assert (repo / ".github" / "CODEOWNERS").read_text("utf-8") == "* @platform-team\n"
    assert any("CODEOWNERS" in n for n in result.notes), result.notes
