"""A candidate's own frontmatter must never steer a harvest write out of its repo.

Skill names and fact topics arrive as distilled, model-generated (or hand-placed) text —
the memory-poisoning threat mneme's README names. They used to be joined straight into a
filesystem path, so a name of `../../kb-b/skills/injected` wrote SKILL.md into a *sibling*
registered repo's working tree and `../../../loose` wrote outside every repo at all.

Both escaped every guard the harvest has: the file lands where `git add -A` cannot see it
(so the batch fails "nothing to commit" and rolls back), `_abort` only restores the target
repo, and lint's kebab-case check (MN002) never runs on a file that never entered the
repo. Net effect: an arbitrary file write, plus a dirtied sibling repo whose next harvest
is wedged on the `is_clean` precondition.
"""
import pytest

from mneme_core import compose, gitops, harvest, paths, scaffold, staging
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id


def skill_body(name: str) -> str:
    """A skill unit whose frontmatter name is `name` — hostile names included.

    Built by hand, not through `compose.render_skill_unit`: composition already refuses a
    non-kebab name, and the whole point here is what `apply_skill` does with a body that
    never went through it (an adopted repo, a hand-written candidate, a distiller that
    wrote staging directly).
    """
    return (
        "---\n"
        f"name: {name}\n"
        "description: Use when deploying the widget service\n"
        "metadata:\n"
        "  mneme-type: skill\n"
        "  mneme-source: demo@s1\n"
        "  mneme-captured: 2026-08-11\n"
        "  mneme-last-verified: 2026-08-11\n"
        "---\n\n"
        "# skill\n\n## Procedure\n\n1. steps\n\n## Failure pattern\n\nwhat failed\n"
    )


def skill_candidate(body: str, target: str = "acme-knowledge", **kw) -> Candidate:
    return Candidate(
        id=candidate_id("skill", target, body), type="skill", edit="new",
        target=target, body=body,
        provenance={"source": "demo@s1", "captured": "2026-08-11"}, **kw,
    )


def fact_candidate(topic: str = "staging-env", edit: str = "new", **kw) -> Candidate:
    body = compose.render_fact_bullet(
        "constraint", "Staging DB resets nightly at 04:00 UTC", ["staging"],
        verified="2026-08-11",
    )
    return Candidate(
        id=candidate_id("fact", "acme-knowledge", body), type="fact", edit=edit,
        target="acme-knowledge", body=body, topic=topic, **kw,
    )


ESCAPES = [
    "../../kb-b/skills/injected",   # into a sibling registered repo
    "../../../loose",               # outside every git repo
    "../sneaky",
    "..",
    "nested/name",                  # a second path segment is not a unit name either
    "/etc/mneme-pwn",               # absolute: `joinpath` would discard the repo root
]


@pytest.mark.parametrize("name", ESCAPES)
def test_apply_skill_refuses_to_write_outside_the_repo(tmp_path, name):
    repo = tmp_path / "kb-a"
    (repo / "skills").mkdir(parents=True)
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())

    with pytest.raises(MnemeError, match="skill name"):
        harvest.apply_skill(repo, skill_candidate(skill_body(name)))

    # Nothing was written anywhere — not in the repo, not beside it.
    assert sorted(p for p in tmp_path.rglob("*") if p.is_file()) == before


def test_apply_skill_refuses_a_non_kebab_name(tmp_path):
    """The name is the directory name; lint (MN002) demands kebab-case of both."""
    repo = tmp_path / "kb-a"
    (repo / "skills").mkdir(parents=True)

    with pytest.raises(MnemeError, match="kebab-case"):
        harvest.apply_skill(repo, skill_candidate(skill_body("Deploy_Widget")))

    assert not (repo / "skills" / "Deploy_Widget").exists()


@pytest.mark.parametrize("topic", ["../../kb-b/facts/injected", "../../../loose", ".."])
def test_apply_fact_refuses_to_write_outside_the_repo(tmp_path, topic):
    repo = tmp_path / "kb-a"
    (repo / "facts").mkdir(parents=True)
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())

    with pytest.raises(MnemeError, match="fact topic"):
        harvest.apply_fact(repo, fact_candidate(topic=topic))

    assert sorted(p for p in tmp_path.rglob("*") if p.is_file()) == before


def test_apply_fact_update_refuses_a_traversing_target_unit(tmp_path):
    """`target_unit` reaches `apply_fact` straight from distiller output."""
    repo = tmp_path / "kb-a"
    (repo / "facts").mkdir(parents=True)
    outside = tmp_path / "loose.md"
    outside.write_text(
        "---\ntopic: loose\n---\n\n- [constraint] Old text #x (verified: 2026-08-01)\n",
        encoding="utf-8",
    )
    original = outside.read_text(encoding="utf-8")

    with pytest.raises(MnemeError, match="fact topic"):
        harvest.apply_fact(
            repo,
            fact_candidate(edit="update", target_unit="facts/../../loose#old-text"),
        )

    assert outside.read_text(encoding="utf-8") == original


def test_harvest_batch_cannot_dirty_a_sibling_knowledge_repo(tmp_path):
    """End to end: the escape used to survive the batch's own rollback.

    `_abort` restores the *target* repo only, so a file written into a sibling repo
    outlived the failed harvest and wedged that repo's next `share apply` on `is_clean`.
    """
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    sibling = scaffold.create(home, "kb-b", owner="demo")
    base = gitops.head_sha(target)
    sibling_base = gitops.head_sha(sibling)

    cand = skill_candidate(skill_body("../../kb-b/skills/injected"))
    staging.write_candidate(home, cand)

    with pytest.raises(MnemeError, match="skill name"):
        harvest.apply_batch(home, "acme-knowledge", [cand], push=False)

    # The sibling repo never saw the write and is still harvestable.
    assert not (sibling / "skills" / "injected").exists()
    assert gitops.is_clean(sibling)
    assert gitops.head_sha(sibling) == sibling_base

    # The target repo is back where the harvest found it, candidate still staged.
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.head_sha(target) == base
    assert "mneme/harvest-" not in gitops.git(target, "branch")
    assert [c.id for c in staging.load_candidates(home)] == [cand.id]
    assert not paths.submitted_path(home).exists()


def test_harvest_batch_cannot_write_outside_every_repo(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    base = gitops.head_sha(target)

    cand = skill_candidate(skill_body("../../../escaped"))
    staging.write_candidate(home, cand)

    with pytest.raises(MnemeError, match="skill name"):
        harvest.apply_batch(home, "acme-knowledge", [cand], push=False)

    assert not any(p.name == "SKILL.md" for p in home.parent.glob("*/SKILL.md"))
    assert not (paths.repos_dir(home).parent / "escaped").exists()
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.head_sha(target) == base


def test_legitimate_names_still_apply(tmp_path):
    """The guard rejects escapes, not ordinary units."""
    repo = tmp_path / "kb-a"
    (repo / "skills").mkdir(parents=True)
    (repo / "facts").mkdir(parents=True)

    line = harvest.apply_skill(repo, skill_candidate(skill_body("deploy-widget-2")))
    assert line == "skills/deploy-widget-2 (new skill)"
    assert (repo / "skills" / "deploy-widget-2" / "SKILL.md").exists()

    assert harvest.apply_fact(repo, fact_candidate(topic="staging-env")).startswith(
        "facts/staging-env#"
    )
    assert (repo / "facts" / "staging-env.md").exists()
