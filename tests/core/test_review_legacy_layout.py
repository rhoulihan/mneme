"""Extraction has to target the layout the repo actually uses (spec §7.7 / §7.8).

The review skill told the agent to write approved bullets into
`skills/knowledge-index/facts/<topic>.md` unconditionally. On a repo whose facts still live
in a top-level `facts/`, that instruction manufactures the one collision the migration
cannot resolve — and the failure landed inside finalize's guarded block, so `harvest._abort`
hard-reset the branch and the user-approved extraction was gone with nothing staged to
retry from. The message then told a review user to "run classify again", a command this
rail does not have.

So: the bundle reports the destination, the collision is caught before anything is touched,
and the remediation names the active rail.
"""
import os
import stat
from pathlib import Path

import pytest

from mneme_core import classify, gitops, review, scaffold, units
from mneme_core.errors import MnemeError

REPO_ROOT = Path(__file__).resolve().parents[2]

EXISTING = "- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-11)"
APPROVED = "- [runbook-note] Sidecar draining requires a preStop hook #sidecar (verified: 2026-08-12)"


def shim_gh(tmp_path, monkeypatch, pr_list_json="[]", diffs=None):
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    (bindir / "prlist.json").write_text(pr_list_json, encoding="utf-8")
    for n, diff in (diffs or {}).items():
        (bindir / f"diff{n}.txt").write_text(diff, encoding="utf-8")
    gh = bindir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *"pr list"*) cat "{bindir}/prlist.json" ;;\n'
        f'  *"pr diff"*) n=$(echo "$@" | grep -o "[0-9]*" | head -1); cat "{bindir}/diff$n.txt" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def make_kb(tmp_path, name, *, legacy, keep_canonical_dir=False):
    """A kb whose `deploys.md` lives in the legacy layout, the canonical one, or both dirs.

    `keep_canonical_dir` is the mixed shape a 0.5 scaffold leaves behind: the canonical
    directory exists (holding only its placeholder) while the topic files are still at the
    top level. That is where hardcoding the canonical path does its damage — the new file
    goes canonical, the old one stays legacy, and the two collide by name.
    """
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo")
    canonical = target / units.FACTS_CANONICAL
    if legacy and not keep_canonical_dir:
        for p in sorted(canonical.rglob("*")):
            p.unlink()
        canonical.rmdir()
    facts = (target / "facts") if legacy else canonical
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "deploys.md").write_text(
        f"---\ntopic: deploys\n---\n{EXISTING}\n", encoding="utf-8"
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "fixtures")
    return home, target


def test_the_bundle_names_the_repos_own_facts_directory(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path, "legacy-kb", legacy=True)
    shim_gh(tmp_path, monkeypatch)

    bundle = review.triage(home, target)

    assert bundle["facts_dir"] == "facts"
    assert bundle["fact_files"] == ["facts/deploys.md"]
    assert bundle["legacy_layout"] is True
    assert "facts_dir" in bundle["instructions"]
    assert "fact_files" in bundle["instructions"]


def test_the_bundle_names_the_canonical_directory_on_a_current_repo(tmp_path, monkeypatch):
    home, target = make_kb(tmp_path, "current-kb", legacy=False)
    shim_gh(tmp_path, monkeypatch)

    bundle = review.triage(home, target)

    assert bundle["facts_dir"] == units.FACTS_CANONICAL
    assert bundle["fact_files"] == [f"{units.FACTS_CANONICAL}/deploys.md"]
    assert bundle["legacy_layout"] is False


def test_a_mixed_repo_still_points_an_existing_topic_at_its_own_file(tmp_path, monkeypatch):
    """`facts_dir` is where a NEW topic goes; an existing one is named in `fact_files`."""
    home, target = make_kb(tmp_path, "mixed-kb", legacy=True, keep_canonical_dir=True)
    shim_gh(tmp_path, monkeypatch)

    bundle = review.triage(home, target)

    assert bundle["facts_dir"] == units.FACTS_CANONICAL
    assert bundle["fact_files"] == ["facts/deploys.md"]


def test_writing_to_the_wrong_layout_does_not_destroy_the_extraction(tmp_path):
    home, target = make_kb(tmp_path, "conflict-kb", legacy=True, keep_canonical_dir=True)
    branch = classify.review_begin(home, target)
    wrong = target / units.FACTS_CANONICAL / "deploys.md"
    wrong.write_text(f"---\ntopic: deploys\n---\n{APPROVED}\n", encoding="utf-8")

    with pytest.raises(MnemeError) as exc:
        classify.review_finalize(home, target, push=False)

    message = str(exc.value)
    assert "both fact layouts carry deploys.md" in message
    # the remediation names THIS rail, not the other one
    assert "mneme review finalize" in message
    assert "classify" not in message
    # and the approved work is still there to fix, on the branch it was made on
    assert wrong.is_file()
    assert APPROVED in wrong.read_text(encoding="utf-8")
    assert gitops.current_branch(target) == branch


def test_the_extraction_finalizes_once_it_targets_the_right_layout(tmp_path):
    """The same pass, written where the bundle points: no collision, no special case."""
    home, target = make_kb(tmp_path, "right-layout-kb", legacy=True)
    classify.review_begin(home, target)
    facts_dir = target / "facts"
    assert units.facts_dir(target) == facts_dir
    (facts_dir / "sidecars.md").write_text(
        f"---\ntopic: sidecars\n---\n{APPROVED}\n", encoding="utf-8"
    )

    result = classify.review_finalize(home, target, push=False)

    assert gitops.current_branch(target) == "main"
    tree = gitops.git(target, "ls-tree", "-r", "--name-only", result.branch).splitlines()
    # finalize migrates the legacy layout as part of the pass — both facts travel together
    assert f"{units.FACTS_CANONICAL}/sidecars.md" in tree
    assert f"{units.FACTS_CANONICAL}/deploys.md" in tree
    assert not any(p.startswith("facts/") for p in tree)


def test_the_skill_routes_writes_through_the_reported_directory():
    body = (REPO_ROOT / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")

    assert "facts_dir" in body
    assert "both fact layouts carry" in body
