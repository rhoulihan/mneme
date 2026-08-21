"""Nothing mneme writes travels through a symlink — on EVERY write path, not just one.

`layout._canonical_dir` proved this and documented at length why it must: a canonical
directory that is itself a link resolves to the far end, so a containment check made
against the *resolved* root is vacuous and every write lands outside the repo while mneme
reports success. That proof existed in exactly one module. The two paths that actually
create files — `harvest._unit_path` and `scaffold.adopt`'s write loop — never asked.

Both are reachable from repo content: a contributor, or a merged pull request, can commit
any path segment as a symlink (git stores mode `120000` happily). Adoption is worse still,
because a *dangling* link is not `exists()`, so the "only add what is missing" test passes
and the write creates the link's target — an arbitrary file at any path the user can write,
chosen by somebody else's repo, leaving no trace in `git status`.
"""
import subprocess

import pytest

from mneme_core import gitops, harvest, registry, scaffold, units
from mneme_core.errors import MnemeError
from mneme_core.registry import Plugin

from tests.core.test_plain_repo_harvest import plain_repo, stage_fact


def outside(tmp_path):
    """A directory that is not the repo, standing in for anything a link can reach."""
    d = tmp_path / "victim"
    d.mkdir()
    return d


def commit(repo, message="fixtures"):
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", message)


# --- the detector itself -----------------------------------------------------


def test_the_first_linked_segment_is_named_not_just_detected(tmp_path):
    """Callers word their own refusal, so the detector returns WHICH segment is a link."""
    repo = tmp_path / "repo"
    (repo / "a").mkdir(parents=True)
    (outside(tmp_path) / "real").mkdir()
    (repo / "a" / "b").symlink_to(tmp_path / "victim" / "real")

    assert units.first_link_segment(repo, "a/b/c.md") == "a/b"
    assert units.first_link_segment(repo, "a/b") == "a/b"
    assert units.first_link_segment(repo, "a/c.md") is None
    assert units.first_link_segment(repo, "nothing/here.md") is None


def test_the_repo_root_itself_is_not_the_question(tmp_path):
    """A clone under a symlinked parent is ordinary; every proof is relative to the root."""
    real = tmp_path / "real-repo"
    (real / "a").mkdir(parents=True)
    link = tmp_path / "linked-repo"
    link.symlink_to(real)
    assert units.first_link_segment(link, "a/x.md") is None


# --- harvest's write path ----------------------------------------------------


@pytest.mark.parametrize("segment", ["mneme-index", "mneme-index/facts"])
def test_a_linked_knowledge_root_refuses_the_harvest(tmp_path, segment):
    """The knowledge would leave the repo while the harvest reported it landed."""
    home, repo = plain_repo(tmp_path)
    away = outside(tmp_path)
    (away / "facts").mkdir()
    target = away if segment == "mneme-index" else away / "facts"
    (repo / segment).parent.mkdir(parents=True, exist_ok=True)
    (repo / segment).symlink_to(target)
    commit(repo, "a contributor commits a symlink")
    main_before = gitops.git(repo, "rev-parse", "main")

    with pytest.raises(MnemeError, match="symlink"):
        harvest.apply_batch(
            home, "payments-service", [stage_fact(home, "payments-service")], push=False
        )

    assert list(away.rglob("*.md")) == [], "a fact was written outside the repo"
    assert gitops.git(repo, "rev-parse", "main") == main_before
    assert gitops.current_branch(repo) == "main"


def test_a_linked_skills_dir_refuses_the_harvest_in_a_plugin(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    away = outside(tmp_path)
    import shutil

    shutil.rmtree(target / "skills")
    (target / "skills").symlink_to(away)
    with pytest.raises(MnemeError, match="symlink"):
        harvest.apply_skill(target, _skill_candidate())
    assert list(away.rglob("SKILL.md")) == []


def _skill_candidate():
    from mneme_core.staging import Candidate, candidate_id

    body = (
        "---\nname: deploy-widget\ndescription: Use when deploying the widget service\n"
        "---\n\n## Procedure\n\nSteps.\n"
    )
    return Candidate(
        id=candidate_id("skill", "t", body), type="skill", edit="new",
        target="t", body=body,
    )


# --- adoption's write path ---------------------------------------------------


def app_repo(tmp_path, home, links=()):
    repo = tmp_path / "payments-service"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    for rel, dest in links:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.symlink_to(dest)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    gitops.git(repo, "config", "user.email", "t@example.com")
    gitops.git(repo, "config", "user.name", "Test")
    commit(repo, "the app")
    registry.add_plugin(home, Plugin(name="payments-service", repo="r", path=str(repo)))
    return repo


def test_a_dangling_link_is_not_a_missing_file(tmp_path):
    """`exists()` follows links, so a dangling one reads as absent and the write CREATES
    the target — an arbitrary file outside the repo, invisible to `git status`."""
    home = tmp_path / "home"
    away = outside(tmp_path)
    victim = away / ".bashrc"  # deliberately absent: the link dangles
    repo = app_repo(tmp_path, home, links=[("MNEME.md", victim)])

    with pytest.raises(MnemeError, match="symlink"):
        scaffold.adopt(home, "payments-service")

    assert not victim.exists(), "adopt wrote through a dangling symlink"


def test_a_linked_parent_directory_redirects_the_write(tmp_path):
    """Same primitive without needing the leaf to dangle: `mkdir(exist_ok=True)` accepts it."""
    home = tmp_path / "home"
    away = outside(tmp_path)
    (away / "workflows").mkdir()
    repo = app_repo(tmp_path, home, links=[(".github/workflows", away / "workflows")])

    with pytest.raises(MnemeError, match="symlink"):
        scaffold.adopt(home, "payments-service")

    assert list((away / "workflows").iterdir()) == [], "adopt wrote outside the repo"


def test_an_ordinary_repo_is_still_adopted(tmp_path):
    """The refusal must not cost the normal case."""
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home)
    result = scaffold.adopt(home, "payments-service")
    assert "MNEME.md" in result.added
    assert (repo / "mneme-index" / "SKILL.md").is_file()


def test_a_failure_partway_through_says_what_already_landed(tmp_path):
    """Adopt is not transactional. Silence about what it wrote is the part that hurts."""
    home = tmp_path / "home"
    repo = app_repo(tmp_path, home)
    (repo / "mneme-index").write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(MnemeError) as e:
        scaffold.adopt(home, "payments-service")

    message = str(e.value)
    assert "mneme-index" in message
    # Whatever it managed to write is named, so the user can undo it.
    if (repo / "MNEME.md").exists():
        assert "MNEME.md" in message


def test_a_fact_stem_cannot_escape_its_facts_directory(tmp_path):
    """`find_fact_file`'s containment proof, tested directly.

    The stem arrives from distiller-authored candidate frontmatter and the result is read
    and printed in a `share view` diff, so this is a read-side traversal defence — and it
    had no test at all.
    """
    home, repo = plain_repo(tmp_path)
    facts = repo / units.FACTS_PLAIN
    facts.mkdir(parents=True)
    (facts / "real.md").write_text("---\ntopic: real\n---\n", encoding="utf-8")
    away = outside(tmp_path)
    (away / "secret.md").write_text("secrets\n", encoding="utf-8")
    (facts / "evil.md").symlink_to(away / "secret.md")

    assert units.find_fact_file(repo, "real") == facts / "real.md"
    assert units.find_fact_file(repo, "../../../victim/secret") is None
    assert units.find_fact_file(repo, "..") is None
    assert units.find_fact_file(repo, "evil") is None, "a symlink out of the repo"


def test_a_non_dict_plugin_manifest_is_a_repo_problem_not_a_traceback(tmp_path):
    """A `plugin.json` containing a JSON array reached `.get` on a list."""
    home, repo = plain_repo(tmp_path)
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "plugin.json").write_text('["not", "an", "object"]\n', encoding="utf-8")
    commit(repo, "a manifest that is not an object")

    with pytest.raises(MnemeError, match="JSON object"):
        harvest.apply_batch(
            home, "payments-service", [stage_fact(home, "payments-service")], push=False
        )
    assert gitops.current_branch(repo) == "main"
