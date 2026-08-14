"""A repo that is not a Claude Code plugin keeps its knowledge in `mneme-index/`.

Registration never required a plugin, so an ordinary app repo could always be registered —
and then bricked on its first fact. `_regenerate_index` returned early without a
`plugin.json`, so `skills/knowledge-index/` was created (by the fact write) with no
`SKILL.md` inside it, and `lint_repo` — which walks every directory under `skills/` —
failed MN001 and rolled the whole harvest back. A repo you can register and cannot use is
worse than one you cannot register.

The layout is a resolution, not a second code path: a plugin writes to
`skills/knowledge-index/`, a plain repo to `mneme-index/`, and every READER accepts both
(plus the pre-0.5 top-level `facts/`). Mode decides where the next byte lands and nothing
else, so a repo that gains or loses a manifest stays fully readable across the change.
"""
import subprocess

import pytest

from mneme_core import gitops, harvest, lint, registry, scaffold, staging, units
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id
from mneme_index import build


def plain_repo(tmp_path, name="payments-service", *, extra=()):
    """An ordinary service repo: source, a README, no plugin manifest anywhere."""
    home = tmp_path / "home"
    repo = tmp_path / name
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (repo / "README.md").write_text(f"# {name}\n\nSettles payments.\n", encoding="utf-8")
    for rel, content in extra:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    gitops.git(repo, "config", "user.email", "t@example.com")
    gitops.git(repo, "config", "user.name", "Test")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "the app")
    registry.add_plugin(
        home, registry.Plugin(name=name, repo=f"git@example.com:acme/{name}.git", path=str(repo))
    )
    return home, repo


def stage_fact(home, target, text="Card refunds settle on the next business day"):
    body = f"- [gotcha] {text} #refunds (verified: 2026-08-14)\n"
    cand = Candidate(
        id=candidate_id("fact", target, body), type="fact", edit="new",
        target=target, body=body, topic="refunds",
        provenance={"source": "demo@s1", "captured": "2026-08-14"},
    )
    staging.write_candidate(home, cand)
    return cand


def test_harvest_into_a_plain_repo_succeeds(tmp_path):
    """The reported bug, end to end: this raised MN001 and rolled back."""
    home, repo = plain_repo(tmp_path)
    main_before = gitops.git(repo, "rev-parse", "main")

    result = harvest.apply_batch(home, "payments-service", [stage_fact(home, "payments-service")], push=False)

    assert gitops.git(repo, "rev-parse", "main") == main_before  # PR-only holds in both modes
    assert gitops.current_branch(repo) == "main"
    tree = gitops.git(repo, "ls-tree", "-r", "--name-only", result.branch).splitlines()
    assert "mneme-index/facts/refunds.md" in tree
    assert "mneme-index/SKILL.md" in tree
    assert not any(t.startswith("skills/") for t in tree)


def test_the_plain_router_is_a_lintable_skill(tmp_path):
    """MN003 requires `name` to match the directory, so the router is named for where it lives."""
    home, repo = plain_repo(tmp_path)
    harvest.apply_batch(home, "payments-service", [stage_fact(home, "payments-service")], push=False)
    gitops.git(repo, "checkout", gitops.git(repo, "branch", "--list", "mneme/harvest-*").strip().lstrip("* "))

    text = (repo / "mneme-index" / "SKILL.md").read_text(encoding="utf-8")
    meta, _ = units.parse_frontmatter(text)
    assert meta["name"] == "mneme-index"
    assert "| refunds | facts/refunds.md | 1 |" in text
    assert not lint.has_errors(lint.lint_repo(repo))


def test_lint_never_walks_an_app_s_own_skills_directory(tmp_path):
    """`skills/` in a plain repo is the app's, not mneme's — mneme does not get to fail it."""
    home, repo = plain_repo(
        tmp_path, extra=[("skills/combat/README.md", "# the character's combat skills\n")]
    )
    assert not lint.has_errors(lint.lint_repo(repo))
    # And a harvest into that repo still completes rather than tripping over it.
    result = harvest.apply_batch(home, "payments-service", [stage_fact(home, "payments-service")], push=False)
    assert "mneme-index/facts/refunds.md" in gitops.git(
        repo, "ls-tree", "-r", "--name-only", result.branch
    ).splitlines()


def test_a_plugin_repo_is_untouched_by_the_new_mode(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    assert units.facts_write_dir(target) == target / units.FACTS_CANONICAL
    assert units.knowledge_root(target) == target / "skills" / "knowledge-index"
    assert (target / "skills" / "knowledge-index" / "SKILL.md").is_file()
    assert not (target / "mneme-index").exists()


def test_the_write_location_follows_the_mode(tmp_path):
    _home, plain = plain_repo(tmp_path)
    assert units.is_plugin(plain) is False
    assert units.facts_write_dir(plain) == plain / "mneme-index" / "facts"
    assert units.knowledge_root(plain) == plain / "mneme-index"

    home2 = tmp_path / "home2"
    plugin = scaffold.create(home2, "kb", owner="demo")
    assert units.is_plugin(plugin) is True


def test_every_reader_accepts_every_layout(tmp_path):
    """Mode picks the write location. Reads sweep all three, so a mode change loses nothing."""
    _home, repo = plain_repo(tmp_path)
    for rel in ("mneme-index/facts", "skills/knowledge-index/facts", "facts"):
        d = repo / rel
        d.mkdir(parents=True)
        (d / f"{d.parent.name}-topic.md").write_text(
            f"---\ntopic: {d.parent.name}-topic\n---\n"
            "- [reference] Something worth keeping #x (verified: 2026-08-14)\n",
            encoding="utf-8",
        )
    found = {str(f.relative_to(repo)) for f in units.fact_files(repo)}
    assert found == {
        "mneme-index/facts/mneme-index-topic.md",
        "skills/knowledge-index/facts/knowledge-index-topic.md",
        "facts/payments-service-topic.md",
    }
    # The repo's OWN layout is offered first, so callers that want one directory get the
    # one new writes go to.
    assert units.facts_dirs(repo)[0] == repo / "mneme-index" / "facts"
    assert units.facts_dir(repo) == repo / "mneme-index" / "facts"


def test_the_index_finds_the_plain_router(tmp_path):
    """A router nothing indexes is a routing table no agent is ever shown."""
    home, repo = plain_repo(tmp_path)
    harvest.apply_batch(home, "payments-service", [stage_fact(home, "payments-service")], push=False)
    gitops.git(repo, "checkout", gitops.git(repo, "branch", "--list", "mneme/harvest-*").strip().lstrip("* "))

    skipped: list[str] = []
    rows = build._skill_rows("payments-service", repo, skipped)
    assert [r for r in rows if "mneme-index" in str(r)], f"router not indexed; skipped={skipped}"


def test_reading_an_app_s_skill_is_free_but_enforcing_on_it_is_not(tmp_path):
    """The asymmetry, stated: indexing costs a row, linting can brick the repo.

    A directory holding a `SKILL.md` is knowledge-shaped, so the index ingests it wherever
    it lives — a hand-built knowledge repo that never grew a manifest keeps its skills
    searchable. Lint is the opposite: `name: combat-arts` in `skills/combat/` is MN003, and
    in a plain repo that would abort a harvest over a file the application owns and mneme
    can neither fix nor be right about.
    """
    _home, repo = plain_repo(
        tmp_path,
        extra=[("skills/combat/SKILL.md", "---\nname: combat-arts\ndescription: x\n---\n")],
    )
    skipped: list[str] = []
    rows = build._skill_rows("payments-service", repo, skipped)
    assert [r for r in rows if "combat" in str(r)], "readable skills are indexed"
    assert not lint.has_errors(lint.lint_repo(repo)), "but never enforced against"

    # In a plugin the same file IS mneme's, and MN003 is exactly the report to make.
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "kb", "version": "0.1.0"}\n', encoding="utf-8"
    )
    assert lint.has_errors(lint.lint_repo(repo))


def test_a_plain_repo_s_legacy_facts_migrate_into_its_own_root(tmp_path):
    """The migration destination is a resolution, not a constant.

    `layout` resolved the directory through `facts_write_dir` and then handed `git mv` the
    hard-coded canonical string, so every rename named a directory the migration was not
    writing into — `git mv` failed and the whole harvest rolled back.
    """
    from mneme_core import layout

    home, repo = plain_repo(tmp_path)
    legacy = repo / "facts"
    legacy.mkdir()
    (legacy / "deploys.md").write_text(
        "---\ntopic: deploys\n---\n"
        "- [gotcha] The drain window is ninety seconds #deploy (verified: 2026-08-14)\n",
        encoding="utf-8",
    )
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "a legacy fact")

    gitops.create_branch(repo, "mneme/migrate-test")
    result = layout.migrate_legacy_facts(repo)

    assert result.moved
    assert (repo / "mneme-index" / "facts" / "deploys.md").is_file()
    assert not (repo / units.FACTS_CANONICAL).exists()
    assert not legacy.exists()
