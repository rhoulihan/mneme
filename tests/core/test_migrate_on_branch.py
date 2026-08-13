"""Migration runs inside every branch flow — nobody has to ask for it (spec §7.3 / §7.7).

Plan 12's directive is that a legacy top-level `facts/` is never accommodated, only
migrated. `layout.migrate_legacy_facts` is the migration and `test_layout_migration.py`
pins what it does; what these tests pin is the WIRING — that every flow which creates a
`mneme/*` branch runs that one function, on the branch, with the existing rollback around
it and the regenerated router skill after it.

Four properties carry the weight:

* **Automatic, and PR-only.** A `share apply` into a pre-0.5 repo migrates it as part of
  the harvest, on the harvest branch, and `main` is byte-identical afterwards.
* **One implementation, three flows.** Harvest, classify and review all call the same
  function — a rail with its own copy is a rail whose merges, symlink refusals and
  containment proofs drift from the tested ones.
* **The Plan 11 preservation gate survives it.** Every fact on `main` sits at a legacy path
  and every fact on the branch sits at a canonical one, so a gate keyed on file paths would
  reject every migration; it is measured across one here, in both directions (a migration
  passes, a deletion during a migration pass is still refused).
* **A failed migration is a failed pass, never a half-migrated repo.** The existing `_abort`
  rail takes it: clean `main`, branch deleted, candidates still staged for the retry.
"""
import json

import pytest

from mneme_core import (
    classify,
    compose,
    gitops,
    harvest,
    layout,
    paths,
    registry,
    scaffold,
    staging,
    units,
)
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id

CANON = units.FACTS_CANONICAL

DEPLOY_TEXT = "Deploys fail when the LB caches dead targets"
QUEUE_TEXT = "The widget queue caps at 500 jobs before shedding"
SIDECAR_TEXT = "Sidecar draining requires a preStop hook"
DRAIN_TEXT = "Blue green cutover needs a 90 second drain"


def bullet(text, category="gotcha", tag="deploy", date="2026-08-12"):
    return f"- [{category}] {text} #{tag} (verified: {date})"


def fact_file(topic, *bullets):
    return f"---\ntopic: {topic}\n---\n" + "".join(b + "\n" for b in bullets)


def make_legacy_kb(tmp_path, name="legacy-kb", *, topics=None, keep_canonical=False):
    """A registered knowledge repo shaped the way a pre-0.5 scaffold left it.

    `keep_canonical` keeps the 0.5 scaffold's canonical directory in place alongside the
    top-level one — the mixed shape, and the only one in which a filename can collide.
    """
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo")
    canonical = target / CANON
    if not keep_canonical:
        for p in sorted(canonical.rglob("*")):
            p.unlink()
        canonical.rmdir()
    legacy = target / "facts"
    legacy.mkdir(parents=True, exist_ok=True)
    for topic, bullets in (topics or {"deploys": [bullet(DEPLOY_TEXT)]}).items():
        (legacy / f"{topic}.md").write_text(fact_file(topic, *bullets), encoding="utf-8")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "legacy facts")
    return home, target


def stage_fact(home, target, topic, text, category="gotcha", tags=("ops",)):
    body = compose.render_fact_bullet(category, text, list(tags), verified="2026-08-12")
    cand = Candidate(
        id=candidate_id("fact", target, body), type="fact", edit="new",
        target=target, body=body, topic=topic,
        provenance={"source": "demo@s1", "captured": "2026-08-12"},
    )
    staging.write_candidate(home, cand)
    return cand


def tree_of(repo, ref):
    return gitops.git(repo, "ls-tree", "-r", "--name-only", ref).splitlines()


def commit_body(repo, ref):
    return gitops.git(repo, "log", ref, "-1", "--format=%b")


def index_rows(text):
    """The routing table of a regenerated knowledge-index SKILL.md, as `[topic, path, n]`."""
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0] in ("Topic", "---"):
            continue
        rows.append(cells)
    return rows


# --- (a) share apply migrates, on the branch, with the notes in the commit body ---------


def test_share_apply_migrates_the_legacy_layout_onto_the_harvest_branch(tmp_path):
    home, target = make_legacy_kb(
        tmp_path,
        topics={"deploys": [bullet(DEPLOY_TEXT)], "queues": [bullet(QUEUE_TEXT, tag="limits")]},
    )
    cand = stage_fact(home, "legacy-kb", "sidecars", SIDECAR_TEXT)

    result = harvest.apply_batch(home, "legacy-kb", [cand], push=False)

    tree = tree_of(target, result.branch)
    # every fact the repo had, plus the new one, in the canonical location
    assert f"{CANON}/deploys.md" in tree
    assert f"{CANON}/queues.md" in tree
    assert f"{CANON}/sidecars.md" in tree
    assert not any(p.startswith("facts/") for p in tree)  # the legacy dir is gone
    assert DEPLOY_TEXT in gitops.git(target, "show", f"{result.branch}:{CANON}/deploys.md")
    assert SIDECAR_TEXT in gitops.git(target, "show", f"{result.branch}:{CANON}/sidecars.md")


def test_the_harvest_branch_is_the_only_thing_that_moves(tmp_path):
    """PR-only: the migration is knowledge movement like any other, so it never sees main."""
    home, target = make_legacy_kb(tmp_path)
    main_before = gitops.git(target, "rev-parse", "main")
    cand = stage_fact(home, "legacy-kb", "sidecars", SIDECAR_TEXT)

    result = harvest.apply_batch(home, "legacy-kb", [cand], push=False)

    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    # main still carries the legacy layout, untouched, until a human merges the branch
    assert "facts/deploys.md" in tree_of(target, "main")
    assert f"{CANON}/deploys.md" not in tree_of(target, "main")
    assert (target / "facts" / "deploys.md").is_file()
    assert gitops.git(target, "rev-parse", f"{result.branch}~1") == main_before


def test_the_commit_body_carries_the_migration_notes_under_a_migrated_section(tmp_path):
    home, target = make_legacy_kb(tmp_path)
    cand = stage_fact(home, "legacy-kb", "sidecars", SIDECAR_TEXT)

    result = harvest.apply_batch(home, "legacy-kb", [cand], push=False)

    body = commit_body(target, result.branch)
    assert "Migrated:" in body
    assert f"- facts/deploys.md -> {CANON}/deploys.md" in body
    unit_line = f"- facts/sidecars#{units.normalize_topic_key(SIDECAR_TEXT)} (new fact)"
    assert unit_line in body
    assert "Mneme-Source: demo@s1" in body
    # ORDER, not just membership. Plan 05's commit_harvest contract closes the message with
    # one blank line and the Mneme-Source trailers, because git only recognises a trailer
    # paragraph at the very end — `git interpret-trailers` and `%(trailers)` stop seeing
    # `Mneme-Source` the moment anything follows it. Appending the Migrated: section after
    # the trailers instead of before them left the whole suite green.
    assert body.index(unit_line) < body.index("Migrated:") < body.index("Mneme-Source:")
    trailer_block = body.rstrip().rsplit("\n\n", 1)[-1]
    assert all(l.startswith("Mneme-Source: ") for l in trailer_block.splitlines()), trailer_block


def test_the_harvests_own_unit_lines_are_unaffected_by_the_migration(tmp_path):
    """`result.units` is the candidates' ledger, not the migration's — Task 3's own words.

    The ledger record is what `mneme share view` and the submitted history report, so a
    migration note leaking into it would restate every moved file as a harvested unit.
    """
    home, target = make_legacy_kb(
        tmp_path,
        topics={"deploys": [bullet(DEPLOY_TEXT)], "queues": [bullet(QUEUE_TEXT, tag="limits")]},
    )
    cand = stage_fact(home, "legacy-kb", "sidecars", SIDECAR_TEXT)

    result = harvest.apply_batch(home, "legacy-kb", [cand], push=False)

    expected = f"facts/sidecars#{units.normalize_topic_key(SIDECAR_TEXT)} (new fact)"
    assert result.units == [expected]
    ledger = [
        json.loads(l)
        for l in paths.submitted_path(home).read_text(encoding="utf-8").splitlines()
    ]
    assert ledger[-1]["units"] == [expected]


def test_a_canonical_repo_gets_no_migration_and_no_migrated_section(tmp_path):
    """The no-op path: `apply_batch` may run the migration unconditionally only because a
    repo with nothing to migrate is not touched by it at all."""
    home = tmp_path / "home"
    target = scaffold.create(home, "current-kb", owner="demo")
    (target / CANON / "deploys.md").write_text(
        fact_file("deploys", bullet(DEPLOY_TEXT)), encoding="utf-8"
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "canonical facts")
    cand = stage_fact(home, "current-kb", "sidecars", SIDECAR_TEXT)

    result = harvest.apply_batch(home, "current-kb", [cand], push=False)

    assert "Migrated:" not in commit_body(target, result.branch)
    changed = gitops.git(
        target, "diff", "--name-only", f"{result.branch}~1", result.branch
    ).splitlines()
    assert f"{CANON}/deploys.md" not in changed  # the untouched fact was not rewritten


def test_a_moved_fact_keeps_its_history_across_the_harvest_branch(tmp_path):
    """`git mv` inside the flow, not a delete plus an add: provenance is the repo's point."""
    home, target = make_legacy_kb(tmp_path)
    cand = stage_fact(home, "legacy-kb", "sidecars", SIDECAR_TEXT)

    result = harvest.apply_batch(home, "legacy-kb", [cand], push=False)

    log = gitops.git(
        target, "log", "--follow", "--format=%s", result.branch, "--", f"{CANON}/deploys.md"
    )
    assert "legacy facts" in log.splitlines()


def test_a_new_bullet_for_a_legacy_topic_lands_in_the_migrated_file(tmp_path):
    """The migration runs BEFORE the candidates are applied, so the append finds the file
    where it now lives — one topic, one file, one unit-id namespace."""
    home, target = make_legacy_kb(tmp_path)
    cand = stage_fact(home, "legacy-kb", "deploys", DRAIN_TEXT, category="runbook-note")

    result = harvest.apply_batch(home, "legacy-kb", [cand], push=False)

    tree = tree_of(target, result.branch)
    assert [p for p in tree if p.endswith("deploys.md")] == [f"{CANON}/deploys.md"]
    text = gitops.git(target, "show", f"{result.branch}:{CANON}/deploys.md")
    assert DEPLOY_TEXT in text and DRAIN_TEXT in text


def test_a_topic_both_layouts_carry_is_merged_by_the_harvest_never_dropped(tmp_path):
    """The one flow that reaches the merge: `apply_batch` has no collision pre-check.

    Never delete knowledge — the legacy bullet the canonical file lacks is appended to it,
    and the note that says so is in the commit body a reviewer reads.
    """
    home, target = make_legacy_kb(tmp_path, keep_canonical=True)
    canonical = target / CANON / "deploys.md"
    canonical.write_text(fact_file("deploys", bullet(DRAIN_TEXT, tag="drain")), encoding="utf-8")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "a canonical deploys topic too")
    cand = stage_fact(home, "legacy-kb", "sidecars", SIDECAR_TEXT)

    result = harvest.apply_batch(home, "legacy-kb", [cand], push=False)

    merged = gitops.git(target, "show", f"{result.branch}:{CANON}/deploys.md")
    assert DRAIN_TEXT in merged and DEPLOY_TEXT in merged
    assert not any(p.startswith("facts/") for p in tree_of(target, result.branch))
    body = commit_body(target, result.branch)
    assert f"- facts/deploys.md merged into {CANON}/deploys.md (1 bullets)" in body


# --- (b) the regenerated router skill is correct for the new location -------------------


def test_the_regenerated_index_routes_to_the_new_location_on_the_branch(tmp_path):
    home, target = make_legacy_kb(
        tmp_path,
        topics={"deploys": [bullet(DEPLOY_TEXT)], "queues": [bullet(QUEUE_TEXT, tag="limits")]},
    )
    cand = stage_fact(home, "legacy-kb", "sidecars", SIDECAR_TEXT)

    result = harvest.apply_batch(home, "legacy-kb", [cand], push=False)

    tree = tree_of(target, result.branch)
    index = gitops.git(target, "show", f"{result.branch}:skills/knowledge-index/SKILL.md")
    rows = index_rows(index)
    assert {topic for topic, _path, _n in rows} == {"deploys", "queues", "sidecars"}
    # every routing path resolves relative to the skill's own directory, which is the whole
    # reason the facts moved inside it — and none of them names the retired location
    for _topic, path, _n in rows:
        assert f"skills/knowledge-index/{path}" in tree
    assert len(rows) == len([p for p in tree if p.startswith(f"{CANON}/") and p.endswith(".md")])


# --- (c) the Plan 11 preservation gate passes across a migration ------------------------


def test_the_preservation_gate_passes_across_a_classify_migration(tmp_path):
    """Every bullet on `main` is at a legacy path; every bullet on the branch is canonical.

    A gate keyed on paths would reject exactly this, which is why it is keyed on sentences —
    and why the migration alone is a finishable classify pass.
    """
    home, target = make_legacy_kb(
        tmp_path,
        name="classify-kb",
        topics={"deploys": [bullet(DEPLOY_TEXT)], "queues": [bullet(QUEUE_TEXT, tag="limits")]},
    )
    main_before = gitops.git(target, "rev-parse", "main")
    classify.begin(home, target)

    result = classify.finalize(home, target, push=False)

    tree = tree_of(target, result.branch)
    assert f"{CANON}/deploys.md" in tree and f"{CANON}/queues.md" in tree
    assert not any(p.startswith("facts/") for p in tree)
    assert DEPLOY_TEXT in gitops.git(target, "show", f"{result.branch}:{CANON}/deploys.md")
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert any("facts/deploys.md ->" in line for line in result.units)


def test_the_preservation_gate_passes_across_a_review_migration(tmp_path):
    """The shared rail: an extraction from a stranger's PR migrates the repo the same way."""
    home, target = make_legacy_kb(tmp_path, name="review-kb")
    classify.review_begin(home, target)
    # the extraction writes where the review bundle points — this repo's own facts dir
    (units.facts_dir(target) / "sidecars.md").write_text(
        fact_file("sidecars", bullet(SIDECAR_TEXT, category="runbook-note", tag="sidecar")),
        encoding="utf-8",
    )

    result = classify.review_finalize(home, target, push=False)

    tree = tree_of(target, result.branch)
    assert f"{CANON}/deploys.md" in tree and f"{CANON}/sidecars.md" in tree
    assert not any(p.startswith("facts/") for p in tree)
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)


def test_a_fact_deleted_during_a_migration_pass_is_still_refused(tmp_path):
    """The gate is not weakened by the migration: a legacy fact that goes nowhere is a loss.

    The migration accounts for a fact by MOVING it. Deleting one in the same pass has to
    still fail, or "every fact on main is somewhere else now" becomes a blanket excuse.
    """
    home, target = make_legacy_kb(
        tmp_path,
        name="loss-kb",
        topics={"deploys": [bullet(DEPLOY_TEXT)], "queues": [bullet(QUEUE_TEXT, tag="limits")]},
    )
    main_before = gitops.git(target, "rev-parse", "main")
    branch = classify.begin(home, target)
    (target / "facts" / "queues.md").unlink()

    with pytest.raises(MnemeError) as exc:
        classify.finalize(home, target, push=False)

    assert QUEUE_TEXT[:80] in str(exc.value)
    assert DEPLOY_TEXT not in str(exc.value)  # the migrated one is accounted for
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.git(target, "branch", "--list", branch) == ""
    assert (target / "facts" / "queues.md").is_file()  # rolled all the way back


# --- one implementation, three flows ---------------------------------------------------


def test_the_harvest_runs_the_shared_migration(tmp_path, monkeypatch):
    home, target = make_legacy_kb(tmp_path)
    cand = stage_fact(home, "legacy-kb", "sidecars", SIDECAR_TEXT)
    calls = []
    real = layout.migrate_legacy_facts
    monkeypatch.setattr(
        layout, "migrate_legacy_facts", lambda repo: (calls.append(repo), real(repo))[1]
    )

    harvest.apply_batch(home, "legacy-kb", [cand], push=False)

    assert [str(c) for c in calls] == [str(target)]


def test_the_finalize_rail_runs_the_shared_migration(tmp_path, monkeypatch):
    """Not a second copy: a rail with its own walk drifts from the tested containment,
    symlink and merge behaviour the moment either side changes."""
    home, target = make_legacy_kb(tmp_path, name="shared-kb")
    classify.begin(home, target)
    calls = []
    real = layout.migrate_legacy_facts
    monkeypatch.setattr(
        layout, "migrate_legacy_facts", lambda repo: (calls.append(repo), real(repo))[1]
    )

    classify.finalize(home, target, push=False)

    assert [str(c) for c in calls] == [str(target)]


# --- (d) a failed migration rolls back like any other failed apply ---------------------


def broken_canonical(target):
    """A regular FILE where the canonical facts directory belongs — a repo-shape problem
    the migration reports rather than a bug: `mkdir` raises FileExistsError under it."""
    canonical = target / CANON
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("not a directory\n", encoding="utf-8")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "a file where the canonical facts dir belongs")


def test_a_failed_migration_rolls_the_harvest_back_and_keeps_staging(tmp_path):
    home, target = make_legacy_kb(tmp_path)
    broken_canonical(target)
    main_before = gitops.git(target, "rev-parse", "main")
    cand = stage_fact(home, "legacy-kb", "sidecars", SIDECAR_TEXT)

    with pytest.raises(MnemeError) as exc:
        harvest.apply_batch(home, "legacy-kb", [cand], push=False)

    assert "facts/deploys.md" in str(exc.value) and CANON in str(exc.value)
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.git(target, "branch", "--list", "mneme/harvest-*") == ""
    # nothing moved, and the candidate is still staged for the identical retry
    assert (target / "facts" / "deploys.md").is_file()
    assert (target / CANON).is_file()
    assert [c.id for c in staging.load_candidates(home)] == [cand.id]


def test_a_failed_migration_rolls_the_finalize_rail_back_too(tmp_path):
    home, target = make_legacy_kb(tmp_path, name="broken-kb")
    broken_canonical(target)
    main_before = gitops.git(target, "rev-parse", "main")
    branch = classify.begin(home, target)

    with pytest.raises(MnemeError) as exc:
        classify.finalize(home, target, push=False)

    assert "facts/deploys.md" in str(exc.value)
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.git(target, "branch", "--list", branch) == ""
    assert (target / "facts" / "deploys.md").is_file()


def test_the_registered_clone_is_the_repo_that_gets_migrated(tmp_path):
    """The migration follows the registry, not the cwd: `share apply` names a target."""
    home, target = make_legacy_kb(tmp_path)
    other = scaffold.create(home, "bystander-kb", owner="demo")
    (other / "facts").mkdir()
    (other / "facts" / "untouched.md").write_text(
        fact_file("untouched", bullet(QUEUE_TEXT, tag="limits")), encoding="utf-8"
    )
    gitops.git(other, "add", "-A")
    gitops.git(other, "commit", "-m", "a legacy layout nobody harvested into")
    cand = stage_fact(home, "legacy-kb", "sidecars", SIDECAR_TEXT)

    harvest.apply_batch(home, "legacy-kb", [cand], push=False)

    assert registry.get_plugin(home, "bystander-kb") is not None
    assert (other / "facts" / "untouched.md").is_file()  # never migrated behind its back
    assert gitops.is_clean(other)
