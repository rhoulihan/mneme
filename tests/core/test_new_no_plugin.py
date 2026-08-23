"""`mneme new --no-plugin` — a knowledge repo that is not distributed as a plugin.

The distinction that matters is NOT the one the flag's name suggests. mneme owns a repo's
`skills/` exactly when its own router lives inside it (`units.maintains_skills`), so a
knowledge repo scaffolded into the PLAIN layout would quietly lose the three things that
make it a knowledge repo: lint would stop enforcing on its skills, `/mneme:classify` would
refuse for want of destination skills, and `harvest.apply_skill` would refuse — it could
hold facts and never skills.

So `--no-plugin` keeps the canonical `skills/knowledge-index/` layout and drops only the
distribution machinery: the plugin manifests and the release workflow that bumps a version
inside one of them. Plain mode (`mneme-index/` at the root) is for an APPLICATION repo,
where `skills/` belongs to the app — a different situation entirely.
"""
import json
import subprocess

import pytest

from mneme_core import classify, gitops, harvest, lint, registry, scaffold, units
from mneme_core.cli import main
from mneme_core.errors import MnemeError

from tests.core.test_plain_repo_harvest import stage_fact


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- what it writes, and what it deliberately does not -----------------------


def test_it_keeps_the_canonical_layout(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "team-kb", owner="demo", as_plugin=False)

    assert (target / units.PLUGIN_ROOT / "SKILL.md").is_file()
    assert (target / units.FACTS_CANONICAL / ".gitkeep").is_file()
    assert not (target / units.PLAIN_ROOT).exists(), "plain mode is for application repos"


def test_it_writes_no_distribution_machinery(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "team-kb", owner="demo", as_plugin=False)

    assert not (target / ".claude-plugin").exists()
    # `release.yml` bumps a version inside a manifest that will not exist.
    assert not (target / ".github" / "workflows" / "release.yml").exists()
    # ...but the format gates matter MORE here, not less: there is no marketplace install
    # to fail loudly, so CI is the only thing that catches a malformed unit.
    assert (target / ".github" / "workflows" / "validate.yml").is_file()


def test_the_governance_files_are_all_still_there(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "team-kb", owner="demo", as_plugin=False)
    for rel in ("MNEME.md", "CONTRIBUTING.md", "CODEOWNERS", "AGENTS.md", "README.md",
                ".gitignore"):
        assert (target / rel).is_file(), rel


def test_the_readme_does_not_promise_a_marketplace_install(tmp_path):
    """There is no manifest, so `/plugin install` cannot reach it. Saying otherwise sends a
    reader to a command that will not work."""
    home = tmp_path / "home"
    target = scaffold.create(home, "team-kb", owner="demo", as_plugin=False)
    text = (target / "README.md").read_text(encoding="utf-8")
    # The absence of a PROMISE, not of the word — the file says there is no marketplace,
    # which is the honest thing to say and mentions it by name.
    assert "Install it through your agent's plugin marketplace tooling" not in text
    assert "no marketplace to install from" in text
    assert "clone it" in text and "mneme registry add" in text, text


# --- the point of the exercise: it is still a knowledge repo -----------------


def test_mneme_still_owns_and_lints_its_skills(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "team-kb", owner="demo", as_plugin=False)

    assert units.is_plugin(target) is False
    assert units.maintains_skills(target) is True
    assert units.knowledge_root(target) == target / units.PLUGIN_ROOT

    bad = target / "skills" / "broken"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: Wrong_Name\n---\n", encoding="utf-8")
    assert lint.has_errors(lint.lint_repo(target)), "skill linting silently switched off"


def test_classify_runs_on_it(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "team-kb", owner="demo", as_plugin=False)
    skill = target / "skills" / "deploy-widget"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: deploy-widget\ndescription: Use when deploying the widget\n---\nBody\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "a skill")

    branch = classify.begin(home, target)
    assert branch.startswith("mneme/classify-")
    assert [s["name"] for s in classify.bundle(home, target)["skills"]] == ["deploy-widget"]
    classify.abort(home, target)


def test_it_can_take_a_skill_and_a_fact(tmp_path):
    from mneme_core import compose
    from mneme_core.staging import Candidate, candidate_id

    home = tmp_path / "home"
    target = scaffold.create(home, "team-kb", owner="demo", as_plugin=False)
    body = compose.render_skill_unit(
        "drain-a-deploy", "Use when draining a deploy", "1. steps", "what failed",
        source="demo@s1", captured="2026-08-23",
    )
    skill = Candidate(
        id=candidate_id("skill", "team-kb", body), type="skill", edit="new",
        target="team-kb", body=body,
    )
    from mneme_core import staging

    staging.write_candidate(home, skill)
    result = harvest.apply_batch(
        home, "team-kb", [skill, stage_fact(home, "team-kb")], push=False
    )
    tree = gitops.git(target, "ls-tree", "-r", "--name-only", result.branch).splitlines()
    assert "skills/drain-a-deploy/SKILL.md" in tree
    assert f"{units.FACTS_CANONICAL}/refunds.md" in tree
    assert not any(t.startswith("mneme-index/") for t in tree)


def test_it_is_registered_and_reported_as_a_plugin_layout(tmp_path, capsys):
    home = tmp_path / "home"
    scaffold.create(home, "team-kb", owner="demo", as_plugin=False)
    assert [p.name for p in registry.load_registry(home)] == ["team-kb"]
    _code, out, _ = run(capsys, "--home", str(home), "status")
    # The mode label reads off the knowledge root, which is the canonical one.
    assert "team-kb" in out and "plugin" in out


# --- promotion ---------------------------------------------------------------


def test_it_can_be_promoted_to_a_real_plugin_later(tmp_path):
    """`adopt --as-plugin` adds the manifests without moving the knowledge root — the
    established-root rule must keep it from creating a second one."""
    home = tmp_path / "home"
    target = scaffold.create(home, "team-kb", owner="demo", as_plugin=False)

    result = scaffold.adopt(home, "team-kb", as_plugin=True)

    assert result.mode == "plugin"
    assert (target / ".claude-plugin" / "plugin.json").is_file()
    assert not (target / units.PLAIN_ROOT).exists(), "a second knowledge root appeared"
    assert units.knowledge_root(target) == target / units.PLUGIN_ROOT
    assert not lint.has_errors(lint.lint_repo(target))


# --- the plugin path is untouched --------------------------------------------


def test_the_default_still_scaffolds_a_full_plugin(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    assert (target / ".claude-plugin" / "plugin.json").is_file()
    assert (target / ".claude-plugin" / "marketplace.json").is_file()
    assert (target / ".github" / "workflows" / "release.yml").is_file()
    assert units.is_plugin(target) is True
    assert "marketplace" in (target / "README.md").read_text(encoding="utf-8").lower()


# --- the CLI -----------------------------------------------------------------


def test_the_cli_flag_scaffolds_the_no_plugin_shape(tmp_path, capsys):
    home = tmp_path / "home"
    code, out, _ = run(capsys, "--home", str(home), "new", "team-kb",
                       "--dir", str(tmp_path / "team-kb"), "--no-plugin")
    assert code == 0
    target = tmp_path / "team-kb"
    assert not (target / ".claude-plugin").exists()
    assert (target / units.PLUGIN_ROOT / "SKILL.md").is_file()
    # And it says which shape it made, since the two differ in what you can DO with the
    # result — a reader who is not told will find out at `/plugin install`.
    assert "no marketplace to install from" in out, out
    assert "mneme adopt team-kb --as-plugin" in out, out


def test_the_cli_default_is_unchanged(tmp_path, capsys):
    home = tmp_path / "home"
    code, _out, _ = run(capsys, "--home", str(home), "new", "acme-knowledge",
                        "--dir", str(tmp_path / "acme-knowledge"))
    assert code == 0
    assert (tmp_path / "acme-knowledge" / ".claude-plugin" / "plugin.json").is_file()


def test_the_first_commit_says_what_it_made(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "team-kb", owner="demo", as_plugin=False)
    subject = gitops.git(target, "log", "-1", "--format=%s")
    assert "plugin" not in subject, subject
