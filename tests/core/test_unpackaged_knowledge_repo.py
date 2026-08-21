"""A knowledge repo that was never packaged as a plugin — the population that already exists.

Curated `skills/`, facts under `skills/knowledge-index/facts/`, no `.claude-plugin/plugin.json`
because nobody ever needed to distribute it. Deciding mode on the manifest alone reclassified
every one of these as "plain" and, in one change: switched off ALL skill linting for them,
refused `/mneme:classify` on them, and pointed the next harvest at a second router directory
Claude Code never discovers — leaving the discoverable one stale and the new one's rows naming
files that do not exist.

Nothing caught it because the tests that would have were the ones the change edited: a plugin
manifest was added to eleven fixtures so they would keep hitting the plugin branch, and nothing
was added to cover the branch they left. This file is that coverage. The rule it pins is
`units.knowledge_root`: a root the repo ALREADY uses wins, whatever the manifest says.
"""
import subprocess

import pytest

from mneme_core import classify, gitops, harvest, lint, registry, scaffold, units
from mneme_core.errors import MnemeError
from mneme_core.registry import Plugin

from tests.core.test_plain_repo_harvest import stage_fact

WRONG_NAME = "---\nname: totally-wrong\ndescription: d\n---\nBody\n"


def unpackaged_kb(tmp_path, *, skill=None, facts=True):
    """Curated skills and canonical facts, and no manifest — an ordinary pre-mneme repo."""
    home = tmp_path / "home"
    repo = tmp_path / "team-kb"
    d = repo / "skills" / "deploy-widget"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        skill or "---\nname: deploy-widget\ndescription: Use when deploying the widget\n---\nBody\n",
        encoding="utf-8",
    )
    if facts:
        f = repo / units.FACTS_CANONICAL
        f.mkdir(parents=True)
        (f / "deploys.md").write_text(
            "---\ntopic: deploys\n---\n"
            "- [gotcha] The drain window is ninety seconds #deploy (verified: 2026-08-14)\n",
            encoding="utf-8",
        )
    (repo / "README.md").write_text("# team-kb\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    gitops.git(repo, "config", "user.email", "t@example.com")
    gitops.git(repo, "config", "user.name", "Test")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "the knowledge repo")
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(repo)))
    return home, repo


def test_its_established_root_is_where_it_already_keeps_things(tmp_path):
    _home, repo = unpackaged_kb(tmp_path)
    assert units.is_plugin(repo) is False, "no manifest — that part is true"
    assert units.knowledge_root(repo) == repo / units.PLUGIN_ROOT
    assert units.facts_write_dir(repo) == repo / units.FACTS_CANONICAL
    assert units.maintains_skills(repo) is True
    assert not (repo / "mneme-index").exists()


def test_its_skills_are_still_linted(tmp_path):
    """MN002/MN003 turned off silently for these repos. Lint enforcing on nothing is worse
    than lint failing: CI stays green while the units it guards rot."""
    _home, repo = unpackaged_kb(tmp_path, skill=WRONG_NAME)
    issues = lint.lint_repo(repo)
    assert lint.has_errors(issues)
    assert {i.code for i in issues} & {"MN003", "MN004"}


def test_a_harvest_lands_in_the_root_it_already_uses(tmp_path):
    """Not a second one. The first router stays discoverable and stays current."""
    home, repo = unpackaged_kb(tmp_path)
    result = harvest.apply_batch(home, "team-kb", [stage_fact(home, "team-kb")], push=False)

    tree = gitops.git(repo, "ls-tree", "-r", "--name-only", result.branch).splitlines()
    assert f"{units.FACTS_CANONICAL}/refunds.md" in tree
    assert not any(t.startswith("mneme-index/") for t in tree), "a second knowledge root"
    router = gitops.git(repo, "show", f"{result.branch}:{units.PLUGIN_ROOT}/SKILL.md")
    assert "| refunds | facts/refunds.md | 1 |" in router
    assert "| deploys | facts/deploys.md | 1 |" in router


def test_classify_still_works_on_it(tmp_path):
    """It has destination skills. Refusing was the manifest check overreaching."""
    home, repo = unpackaged_kb(tmp_path)
    branch = classify.begin(home, repo)
    assert branch.startswith("mneme/classify-")
    bundle = classify.bundle(home, repo)
    assert [s["name"] for s in bundle["skills"]] == ["deploy-widget"]
    classify.abort(home, repo)


def test_a_skill_candidate_is_accepted_by_it(tmp_path):
    """`apply_skill` refuses where mneme does not own `skills/`. Here it does."""
    from mneme_core import compose
    from mneme_core.staging import Candidate, candidate_id

    home, repo = unpackaged_kb(tmp_path)
    body = compose.render_skill_unit(
        "drain-a-deploy", "Use when draining a deploy", "1. steps", "what failed",
        source="demo@s1", captured="2026-08-14",
    )
    cand = Candidate(
        id=candidate_id("skill", "team-kb", body), type="skill", edit="new",
        target="team-kb", body=body,
    )
    result = harvest.apply_batch(home, "team-kb", [cand], push=False)
    tree = gitops.git(repo, "ls-tree", "-r", "--name-only", result.branch).splitlines()
    assert "skills/drain-a-deploy/SKILL.md" in tree


def test_adopting_it_keeps_the_root_it_has(tmp_path):
    """`_adopt_mode` argued this repo IS a knowledge repo while `knowledge_root` said
    otherwise. They now give the same answer."""
    home, repo = unpackaged_kb(tmp_path)
    result = scaffold.adopt(home, "team-kb")
    assert result.mode == "plugin"
    assert not (repo / "mneme-index").exists()
    assert any("skills/knowledge-index" in n for n in result.notes), result.notes


def test_a_repo_with_no_established_root_still_falls_back_to_the_manifest(tmp_path):
    """The manifest decides where the FIRST root goes — it just does not relocate one."""
    _home, repo = unpackaged_kb(tmp_path, facts=False)
    import shutil

    shutil.rmtree(repo / "skills")
    assert units.knowledge_root(repo) == repo / units.PLAIN_ROOT
    assert units.maintains_skills(repo) is False


def test_debris_is_not_a_declaration(tmp_path):
    """An EMPTY `skills/knowledge-index/` is what the original harvest bug left untracked.

    Reading it as "this repo uses the canonical root" would move a plain repo's knowledge
    into a directory nothing ever put anything in — and linting it would fire MN001 on that
    debris and abort every harvest, which is the original bug wearing a different hat.
    """
    from tests.core.test_plain_repo_harvest import plain_repo

    home, repo = plain_repo(tmp_path)
    (repo / units.FACTS_CANONICAL).mkdir(parents=True)

    assert units.knowledge_root(repo) == repo / units.PLAIN_ROOT
    assert units.maintains_skills(repo) is False
    assert not lint.has_errors(lint.lint_repo(repo))
    result = harvest.apply_batch(
        home, "payments-service", [stage_fact(home, "payments-service")], push=False
    )
    assert f"{units.FACTS_PLAIN}/refunds.md" in gitops.git(
        repo, "ls-tree", "-r", "--name-only", result.branch
    ).splitlines()


def test_a_routerless_own_root_is_still_reported(tmp_path):
    """`skill_dirs` walks the repo's own root whether or not it has a SKILL.md — a missing
    router THERE is the real defect MN001 exists to name."""
    from tests.core.test_plain_repo_harvest import plain_repo

    _home, repo = plain_repo(tmp_path)
    facts = repo / units.FACTS_PLAIN
    facts.mkdir(parents=True)
    (facts / "x.md").write_text(
        "---\ntopic: x\n---\n- [reference] A fact #x (verified: 2026-08-14)\n", encoding="utf-8"
    )
    assert units.knowledge_root(repo) == repo / units.PLAIN_ROOT
    issues = lint.lint_repo(repo)
    assert any(i.code == "MN001" for i in issues), [i.code for i in issues]
