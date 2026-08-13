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

And `mneme status` — the command a human runs to find out what is pending — names every
registered plugin still carrying the old layout, so the answer to "which repos need this?"
is not a manual `ls` across every clone.
"""
import json
import os
import stat
import subprocess
from datetime import datetime, timezone

from mneme_core import classify, gitops, paths, registry, scaffold, units
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


def make_legacy_kb(tmp_path, name="legacy-kb", *, topics=None, keep_canonical=False):
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
    home, target = make_legacy_kb(
        tmp_path,
        topics={"deploys": [bullet(DEPLOY_TEXT)], "queues": [bullet(QUEUE_TEXT, tag="limits")]},
    )

    run(capsys, "--home", str(home), "migrate", "--cwd", str(target), "--no-push")

    branch = branches(target)[0]
    index = gitops.git(target, "show", f"{branch}:skills/knowledge-index/SKILL.md")
    rows = [
        [c.strip() for c in line.strip("|").split("|")]
        for line in index.splitlines()
        if line.startswith("|")
    ]
    rows = [r for r in rows if r[0] not in ("Topic", "---")]
    assert {r[0] for r in rows} == {"deploys", "queues"}
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
