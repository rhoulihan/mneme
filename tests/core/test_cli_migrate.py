"""`mneme migrate` — the whole rail for a repo whose only pending change is its layout.

Plan 12 makes migration automatic inside every branch flow, which covers every repo that
still has something to contribute. A knowledge plugin that is simply *old* — a pre-0.5
scaffold nobody has harvested into since — has nothing to trigger that flow, so the
directive ("when mneme detects a facts folder at the root it should always move it") needs
one command that can be the trigger itself.

What these tests pin is that the command is the SAME rail, not a shortcut around it:

* **PR-only, like everything else.** The move lands on a `mneme/migrate-*` branch; `main`
  is byte-identical afterwards and still carries the legacy layout until a human merges.
* **Gated, like everything else.** Index regeneration, lint, secret scan and the
  preservation gate all run — a migration is knowledge movement, and knowledge movement is
  what those gates exist for.
* **Atomic.** There is no judgement to make between begin and finalize, so the command runs
  the rail end to end and every failure path leaves a clean `main` with no branch behind.
* **Honest about having nothing to do.** A canonical repo is told so, in one sentence, and
  is left exactly as it was found.
* **Safe to read.** The commit body and the pull request body are written from repo
  content — legacy filenames — with no agent anywhere in the flow, so a name cannot forge
  lines of its own and a repo with hundreds of topics cannot grow a body past the size at
  which `gh pr create` quietly stops opening the pull request this whole rail exists for.

And `mneme status` — the command a human runs to find out what is pending — names every
registered plugin still carrying the old layout, so the answer to "which repos need this?"
is not a manual `ls` across every clone.
"""
import json
import os
import stat
import subprocess
from datetime import datetime, timezone

from mneme_core import classify, gitops, layout, paths, registry, scaffold, units
from mneme_core.cli import main
from mneme_core.registry import Plugin

# The user-visible location, spelled out: these tests are the contract for where a
# migrated fact ENDS UP, so they must fail if that constant ever moves.
CANON = "skills/knowledge-index/facts"

DEPLOY_TEXT = "Deploys fail when the LB caches dead targets"
QUEUE_TEXT = "The widget queue caps at 500 jobs before shedding"
DRAIN_TEXT = "Blue green cutover needs a 90 second drain"


def bullet(text, category="gotcha", tag="deploy", date="2026-08-12"):
    return f"- [{category}] {text} #{tag} (verified: {date})"


def fact_file(topic, *bullets):
    return f"---\ntopic: {topic}\n---\n" + "".join(b + "\n" for b in bullets)


def legacy_index_skill(name, rows):
    """The router skill a pre-0.5 repo really carries: a table describing its OWN layout.

    `scaffold.create` leaves an EMPTY table, and an empty table is the one fixture that
    cannot tell a regenerated index from a stale one — because the File column is written
    relative to the skill directory (`facts/<t>.md`), which is byte-identical to the
    repo-root path a legacy index recorded. A test built on the empty table therefore
    passes with the regeneration deleted; the rows below are what make the difference
    visible, so each caller seeds a table that is WRONG for the post-migration repo.
    """
    described = (
        f"Consult when you need durable facts from {name} — constraints, gotchas,"
        f" decisions, and runbook notes. Institutional knowledge maintained with mneme:"
        f" {name}. {len(rows)} topics, listed in this skill, stored in facts/."
    )
    table = "".join(f"| {t} | facts/{t}.md | {n} |\n" for t, n in rows)
    return (
        f'---\nname: knowledge-index\ndescription: "{described}"\n---\n\n'
        f"# {name} fact index\n\n"
        "Regenerated mechanically by mneme — do not edit by hand.\n\n"
        "| Topic | File | Bullets |\n|---|---|---|\n" + table
    )


def index_rows_of(text):
    rows = [
        [c.strip() for c in line.strip("|").split("|")]
        for line in text.splitlines()
        if line.startswith("|")
    ]
    return [r for r in rows if r[0] not in ("Topic", "---")]


def make_legacy_kb(
    tmp_path, name="legacy-kb", *, topics=None, keep_canonical=False, index_rows=None
):
    """A registered knowledge repo shaped the way a pre-0.5 scaffold left it."""
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo")
    canonical = target / units.FACTS_CANONICAL
    if not keep_canonical:
        for p in sorted(canonical.rglob("*")):
            p.unlink()
        canonical.rmdir()
    legacy = target / "facts"
    legacy.mkdir(parents=True, exist_ok=True)
    for topic, bullets in (topics or {"deploys": [bullet(DEPLOY_TEXT)]}).items():
        (legacy / f"{topic}.md").write_text(fact_file(topic, *bullets), encoding="utf-8")
    if index_rows is not None:
        (target / "skills" / "knowledge-index" / "SKILL.md").write_text(
            legacy_index_skill(name, index_rows), encoding="utf-8"
        )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "legacy facts")
    return home, target


def make_canonical_kb(tmp_path, name="current-kb"):
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo")
    (target / units.FACTS_CANONICAL / "deploys.md").write_text(
        fact_file("deploys", bullet(DEPLOY_TEXT)), encoding="utf-8"
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "canonical facts")
    return home, target


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def branches(repo, pattern="mneme/*"):
    # `gitops.git` strips its output, so the first line has already lost the two-space
    # prefix `git branch` pads with — parse defensively rather than by column.
    return [
        line.strip().lstrip("* ").strip()
        for line in gitops.git(repo, "branch", "--list", pattern).splitlines()
        if line.strip()
    ]


def tree_of(repo, ref):
    return gitops.git(repo, "ls-tree", "-r", "--name-only", ref).splitlines()


def add_remote(target):
    remote = target.parent / f"{target.name}-remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    gitops.git(target, "remote", "add", "origin", str(remote))
    gitops.git(target, "push", "-u", "origin", "main")
    return remote


def shim_gh(tmp_path, monkeypatch, script):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(script, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def ledger(home):
    return [
        json.loads(line)
        for line in paths.submitted_path(home).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --- the migration lands on its own branch, and main never moves -----------------------


def test_migrate_moves_a_legacy_repo_onto_a_migrate_branch(tmp_path, capsys):
    home, target = make_legacy_kb(
        tmp_path,
        topics={"deploys": [bullet(DEPLOY_TEXT)], "queues": [bullet(QUEUE_TEXT, tag="limits")]},
    )

    code, out, err = run(
        capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push"
    )

    assert code == 0, err
    assert len(branches(target)) == 1
    branch = branches(target)[0]
    assert branch.startswith("mneme/migrate-")
    assert branch in out
    tree = tree_of(target, branch)
    assert f"{CANON}/deploys.md" in tree and f"{CANON}/queues.md" in tree
    assert not any(p.startswith("facts/") for p in tree)
    assert DEPLOY_TEXT in gitops.git(target, "show", f"{branch}:{CANON}/deploys.md")


def test_main_is_untouched_and_still_legacy_until_a_human_merges(tmp_path, capsys):
    home, target = make_legacy_kb(tmp_path)
    main_before = gitops.git(target, "rev-parse", "main")

    run(capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push")

    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert "facts/deploys.md" in tree_of(target, "main")
    assert (target / "facts" / "deploys.md").is_file()
    assert gitops.git(target, "rev-parse", f"{branches(target)[0]}~1") == main_before


def test_the_commit_names_the_pass_a_migration_and_carries_its_notes(tmp_path, capsys):
    home, target = make_legacy_kb(tmp_path)

    run(capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push")

    branch = branches(target)[0]
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert gitops.git(target, "log", branch, "-1", "--format=%s") == f"knowledge: migrate {date}"
    body = gitops.git(target, "log", branch, "-1", "--format=%b")
    assert f"- facts/deploys.md -> {CANON}/deploys.md" in body
    assert "Mneme-Migrate: legacy-kb" in body


def test_the_branch_carries_a_router_skill_that_points_at_the_new_location(tmp_path, capsys):
    """The routing surface is REGENERATED by the pass, not carried across from main.

    The fixture is a real pre-0.5 index rather than the empty table `scaffold.create`
    leaves, because the File column is skill-relative: `facts/deploys.md` is what the
    generator writes AND what the legacy repo already recorded, so a table that merely
    lists the right topics proves nothing. What the table below says that a regenerated
    one cannot — a topic that no longer exists, a bullet count that never got updated, and
    a description claiming three topics — is what makes the assertion able to fail.
    """
    home, target = make_legacy_kb(
        tmp_path,
        topics={
            "deploys": [bullet(DEPLOY_TEXT), bullet(DRAIN_TEXT, tag="drain")],
            "queues": [bullet(QUEUE_TEXT, tag="limits")],
        },
        index_rows=[("deploys", 1), ("queues", 1), ("retired", 3)],
    )
    stale = gitops.git(target, "show", "main:skills/knowledge-index/SKILL.md")

    run(capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push")

    branch = branches(target)[0]
    index = gitops.git(target, "show", f"{branch}:skills/knowledge-index/SKILL.md")
    assert index != stale
    rows = index_rows_of(index)
    assert {r[0] for r in rows} == {"deploys", "queues"}
    assert {r[0]: r[2] for r in rows} == {"deploys": "2", "queues": "1"}
    described = str(units.parse_frontmatter(index)[0]["description"])
    assert "2 topics, listed in this skill" in described
    tree = tree_of(target, branch)
    for row in rows:
        assert f"skills/knowledge-index/{row[1]}" in tree


def test_the_ledger_records_the_pass_as_a_migration(tmp_path, capsys):
    home, target = make_legacy_kb(tmp_path)

    run(capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push")

    record = ledger(home)[-1]
    assert record["kind"] == "migrate"
    assert record["target"] == "legacy-kb"
    assert record["branch"] == branches(target)[0]
    assert f"facts/deploys.md -> {CANON}/deploys.md" in record["units"]


def test_a_topic_both_layouts_carry_is_merged_never_refused(tmp_path, capsys):
    """No author to ask: the user's whole request was "migrate", and the collision is
    committed history rather than an edit some agent just made, so the merge is the
    answer — the same one the harvest takes."""
    home, target = make_legacy_kb(tmp_path, keep_canonical=True)
    (target / units.FACTS_CANONICAL / "deploys.md").write_text(
        fact_file("deploys", bullet(DRAIN_TEXT, tag="drain")), encoding="utf-8"
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "a canonical deploys topic too")

    code, out, err = run(
        capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push"
    )

    assert code == 0, err
    branch = branches(target)[0]
    merged = gitops.git(target, "show", f"{branch}:{CANON}/deploys.md")
    assert DRAIN_TEXT in merged and DEPLOY_TEXT in merged
    assert not any(p.startswith("facts/") for p in tree_of(target, branch))


# --- nothing to migrate is a sentence, not a branch ------------------------------------


def test_a_canonical_repo_is_told_it_has_nothing_to_migrate(tmp_path, capsys):
    home, target = make_canonical_kb(tmp_path)
    main_before = gitops.git(target, "rev-parse", "main")

    code, _out, err = run(
        capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push"
    )

    assert code == 1
    assert "no legacy facts directory — nothing to migrate" in err
    assert branches(target) == []
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert not paths.submitted_path(home).exists() or ledger(home) == []


def test_the_second_migrate_after_the_pull_request_is_merged_has_nothing_to_do(
    tmp_path, capsys
):
    """PR-only means the first run cannot be idempotent on its own: `main` still carries
    the legacy layout until the human merges the branch. Once they do, the command says so
    instead of proposing the same move again."""
    home, target = make_legacy_kb(tmp_path)
    run(capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push")
    first = branches(target)[0]
    gitops.git(target, "merge", "--ff-only", first)
    gitops.git(target, "branch", "-D", first)

    code, _out, err = run(
        capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push"
    )

    assert code == 1
    assert "no legacy facts directory — nothing to migrate" in err
    assert branches(target) == []


def test_an_unregistered_directory_fails_with_the_standard_message(tmp_path, capsys):
    home = tmp_path / "home"
    outside = tmp_path / "somewhere"
    outside.mkdir()

    code, _out, err = run(capsys, "--home", str(home), "migrate", "--cwd", str(outside))

    assert code == 1
    assert "not inside a registered knowledge plugin" in err
    assert "/mneme:register" in err


def test_migrate_refuses_while_another_rail_is_active(tmp_path, capsys):
    """One working tree, three rails: a migration started mid-classify would deliver the
    librarian's half-finished edits as part of the move."""
    home, target = make_legacy_kb(tmp_path, name="busy-kb")
    classify.begin(home, target)

    code, _out, err = run(
        capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push"
    )

    assert code == 1
    assert "already active" in err
    assert branches(target, "mneme/migrate-*") == []


def test_the_migration_goes_through_the_same_secret_scan_as_every_other_pass(
    tmp_path, capsys
):
    """A move is a write: the file lands at a new path, in a commit mneme authors, on a
    branch mneme offers to push. Carrying a credential across that boundary unscanned
    because "nothing changed" would be the one flow that opts out of the gate."""
    home, target = make_legacy_kb(
        tmp_path,
        name="leaky-kb",
        topics={"creds": [bullet("The old key AKIAIOSFODNN7EXAMPLE was rotated", tag="ops")]},
    )
    main_before = gitops.git(target, "rev-parse", "main")

    code, _out, err = run(
        capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push"
    )

    assert code == 1
    assert "fails the secret scan" in err
    assert branches(target) == []
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert (target / "facts" / "creds.md").is_file()


def test_a_migration_that_drops_a_bullet_is_refused_by_the_preservation_gate(
    tmp_path, monkeypatch, capsys
):
    """Migration is the one pass whose ENTIRE job is moving knowledge, so it is the one
    pass that most needs the gate that says knowledge may move but never vanish. There is
    no agent on this rail to make that mistake, which is exactly why the gate has to be
    pinned here: a lossy move could only come from mneme's own code, and nothing else
    would catch it before the commit."""
    home, target = make_legacy_kb(
        tmp_path,
        name="lossy-kb",
        topics={"deploys": [bullet(DEPLOY_TEXT), bullet(DRAIN_TEXT, tag="drain")]},
    )
    main_before = gitops.git(target, "rev-parse", "main")
    real_migration = layout.migrate_legacy_facts

    def lossy(repo):
        result = real_migration(repo)
        moved = repo / units.FACTS_CANONICAL / "deploys.md"
        kept = [
            line
            for line in moved.read_text(encoding="utf-8").splitlines()
            if DRAIN_TEXT not in line
        ]
        moved.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        return result

    monkeypatch.setattr(layout, "migrate_legacy_facts", lossy)

    code, _out, err = run(
        capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push"
    )

    assert code == 1
    assert "migrate would lose knowledge that is committed on main" in err
    assert DRAIN_TEXT in err
    assert branches(target) == []
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert DRAIN_TEXT in (target / "facts" / "deploys.md").read_text(encoding="utf-8")


def test_a_legacy_repo_that_fails_lint_is_refused_before_anything_is_committed(
    tmp_path, capsys
):
    """A pre-0.5 repo is exactly where a malformed fact file survives — nothing has linted
    it since it was written. Moving it is still a write mneme signs its name to, so the
    migrate rail runs the same lint as every other pass rather than waving the repo
    through on the grounds that it only changed location."""
    home, target = make_legacy_kb(tmp_path, name="unlinted-kb")
    (target / "facts" / "orphan.md").write_text(
        "---\nowner: demo\n---\n" + bullet(QUEUE_TEXT, tag="limits") + "\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "a fact file with no topic header")
    main_before = gitops.git(target, "rev-parse", "main")

    code, _out, err = run(
        capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push"
    )

    assert code == 1
    assert "migrate fails repo lint" in err
    assert "MN009" in err
    assert branches(target) == []
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert (target / "facts" / "orphan.md").is_file()


# --- the body a human reads is written from repo content, so it is bounded and escaped ---


# A legacy filename is repo content: whatever a contributor, or a merged pull request,
# committed into `facts/`. Newlines are legal in it, and `mneme migrate` puts it in a
# commit body and a pull request body with no agent anywhere in the flow — so a name can
# try to be four lines: a forged git trailer and an invented finding under the real ones.
FORGED_NAME = (
    "deploys\nMneme-Review: approved by security\n"
    "- forged: the migration deleted nothing\nx.md"
)


def test_a_filename_cannot_forge_extra_lines_in_the_commit_or_pull_request_body(
    tmp_path, capsys
):
    home, target = make_legacy_kb(tmp_path, name="hostile-kb")
    (target / "facts" / FORGED_NAME).write_text(
        fact_file("hostile", bullet(QUEUE_TEXT, tag="limits")), encoding="utf-8"
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "a fact file whose NAME is four lines")

    code, _out, err = run(
        capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push"
    )

    assert code == 0, err
    body = gitops.git(target, "log", branches(target)[0], "-1", "--format=%b")
    assert not any(line.startswith("Mneme-Review:") for line in body.splitlines())
    assert not any(
        line.strip() == "- forged: the migration deleted nothing"
        for line in body.splitlines()
    )
    # The same list is the pull request body and the ledger record, so the property is the
    # list's, not the commit template's: one repo-derived path is one line.
    reported = ledger(home)[-1]["units"]
    assert all("\n" not in unit for unit in reported)
    # And named ONCE. The migration's own note already reports this file; the raw changed
    # path reaching the body alongside it is the duplicate report the dedup exists to stop.
    assert sum(1 for unit in reported if "x.md" in unit) == 1


def test_hundreds_of_legacy_topics_still_produce_a_commit_and_a_reviewable_body(
    tmp_path, capsys
):
    """A mature pre-0.5 repo is the whole reason this command exists, and it is the case
    where the body written from one line per changed path stops being deliverable: past
    ~65 KB `gh pr create` is refused and the pull request — the only human gate this
    migration has — is silently replaced by a "push it yourself" message, while a long
    enough `git commit -m` argument fails outright with E2BIG and rolls the pass back."""
    # The counter leads the stem, not trails it: `_safe` caps a note value at 160
    # characters, so 320 names that differ only in a suffix past that cap all collapse to
    # one string — every path then matches some note, the de-duplication swallows the
    # whole list, and a test built that way passes with the bound deleted.
    stem = "-".join(["deploy"] * 25)
    home, target = make_legacy_kb(
        tmp_path,
        name="mature-kb",
        topics={f"{i:03d}-{stem}": [bullet(DEPLOY_TEXT)] for i in range(320)},
    )

    code, _out, err = run(
        capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push"
    )

    assert code == 0, err
    reported = ledger(home)[-1]["units"]
    assert len("\n".join(reported)) <= 65_000
    assert len(gitops.git(target, "log", branches(target)[0], "-1", "--format=%b")) <= 65_000
    # Bounded, never silently: what was left out is counted in the body itself.
    assert any("omitted to keep this body" in unit for unit in reported)
    # And the migration itself is complete — the bound is on the REPORT, not the work.
    tree = tree_of(target, branches(target)[0])
    assert sum(1 for path in tree if path.startswith(f"{CANON}/")) == 320
    assert not any(path.startswith("facts/") for path in tree)


# --- delivery: a pull request when there is a remote, a local branch when there is not --


def test_migrate_pushes_and_opens_a_pull_request_when_a_remote_exists(
    tmp_path, monkeypatch, capsys
):
    home, target = make_legacy_kb(tmp_path)
    remote = add_remote(target)
    remote_main_before = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "main"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    shim_gh(tmp_path, monkeypatch, "#!/bin/sh\necho https://github.com/acme/kb/pull/12\n")

    code, out, err = run(capsys, "--home", str(home), "migrate", "--cwd", str(target))

    assert code == 0, err
    assert "pr: https://github.com/acme/kb/pull/12" in out
    remote_branches = subprocess.run(
        ["git", "-C", str(remote), "branch"], check=True, capture_output=True, text=True
    ).stdout
    assert branches(target)[0] in remote_branches
    remote_main_after = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "main"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert remote_main_after == remote_main_before


def test_no_push_leaves_the_branch_local_even_with_a_remote(tmp_path, monkeypatch, capsys):
    home, target = make_legacy_kb(tmp_path)
    remote = add_remote(target)
    shim_gh(tmp_path, monkeypatch, "#!/bin/sh\necho https://github.com/acme/kb/pull/13\n")

    code, out, err = run(
        capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push"
    )

    assert code == 0, err
    assert "push skipped (--no-push)" in out
    remote_branches = subprocess.run(
        ["git", "-C", str(remote), "branch"], check=True, capture_output=True, text=True
    ).stdout
    assert "mneme/migrate-" not in remote_branches


# --- status names the repos that still need it ------------------------------------------


def test_status_reports_a_plugin_with_a_legacy_facts_layout(tmp_path, capsys):
    home = tmp_path / "home"
    kb = tmp_path / "old-kb"
    (kb / "facts").mkdir(parents=True)
    registry.add_plugin(home, Plugin(name="old-kb", repo="r", path=str(kb)))

    code, out, _err = run(capsys, "--home", str(home), "status")

    assert code == 0
    assert "legacy facts layout: old-kb (run: mneme migrate in that repo)" in out


def test_status_says_nothing_about_a_canonical_plugin(tmp_path, capsys):
    home = tmp_path / "home"
    kb = tmp_path / "new-kb"
    (kb / "skills" / "knowledge-index" / "facts").mkdir(parents=True)
    registry.add_plugin(home, Plugin(name="new-kb", repo="r", path=str(kb)))

    code, out, _err = run(capsys, "--home", str(home), "status")

    assert code == 0
    assert "legacy facts layout" not in out


def test_status_names_every_legacy_plugin_and_only_those(tmp_path, capsys):
    home = tmp_path / "home"
    for name, sub in (("old-a", "facts"), ("new-b", "skills"), ("old-c", "facts")):
        (tmp_path / name / sub).mkdir(parents=True)
        registry.add_plugin(home, Plugin(name=name, repo="r", path=str(tmp_path / name)))

    code, out, _err = run(capsys, "--home", str(home), "status")

    assert code == 0
    reported = [l for l in out.splitlines() if l.startswith("legacy facts layout:")]
    assert reported == [
        "legacy facts layout: old-a (run: mneme migrate in that repo)",
        "legacy facts layout: old-c (run: mneme migrate in that repo)",
    ]


def test_status_degrades_silently_when_a_clone_is_missing(tmp_path, capsys):
    """status is what a human runs when things already look wrong — a registry entry
    pointing at a directory that is not there must not be the thing that breaks it."""
    home = tmp_path / "home"
    registry.add_plugin(
        home, Plugin(name="gone-kb", repo="r", path=str(tmp_path / "not-cloned"))
    )

    code, out, err = run(capsys, "--home", str(home), "status")

    assert code == 0, err
    assert "- gone-kb [internal]" in out
    assert "legacy facts layout" not in out
