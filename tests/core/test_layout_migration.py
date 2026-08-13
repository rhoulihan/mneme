"""`layout.migrate_legacy_facts` — a legacy top-level `facts/` becomes the canonical dir.

The migration is the one operation that touches every fact a pre-0.5 repo owns at once,
so these tests are written around the two ways it could go wrong: losing knowledge (a
bullet dropped in a merge, a file deleted rather than moved, a subdirectory left behind)
and losing containment (a legacy filename is repo content — a hostile PR can commit one).
"""
import subprocess

import pytest

from mneme_core import gitops, layout, units
from mneme_core.errors import MnemeError

CANON = units.FACTS_CANONICAL


def git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(repo), *args],
        check=True, capture_output=True,
    )


def make_repo(tmp_path):
    repo = tmp_path / "kb"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "README.md").write_text("kb\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "seed")
    return repo


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def bullet(text, tag="deploy", category="gotcha", date="2026-08-12"):
    return f"- [{category}] {text} #{tag} (verified: {date})"


def fact(topic, *bullets):
    return f"---\ntopic: {topic}\n---\n" + "".join(b + "\n" for b in bullets)


def test_a_tracked_legacy_file_moves_and_keeps_its_history(tmp_path):
    repo = make_repo(tmp_path)
    write(repo / "facts" / "deploys.md", fact("deploys", bullet("the lb keeps stale targets")))
    commit(repo, "add the deploys fact")

    result = layout.migrate_legacy_facts(repo)

    assert result.moved == [f"facts/deploys.md -> {CANON}/deploys.md"]
    assert result.merged == []
    assert result.removed_dir is True
    assert not (repo / "facts").exists()
    assert "the lb keeps stale targets" in (repo / CANON / "deploys.md").read_text(
        encoding="utf-8"
    )
    # Staged as a RENAME, not delete+add: that is what carries the file's history across.
    status = gitops.git(repo, "status", "--porcelain").splitlines()
    assert [s for s in status if s.startswith("R ")], status
    commit(repo, "migrate")  # the caller owns the commit; history is followable after it
    log = gitops.git(repo, "log", "--follow", "--format=%s", "--", f"{CANON}/deploys.md")
    assert "add the deploys fact" in log.splitlines()


def test_an_untracked_legacy_file_is_moved_too(tmp_path):
    repo = make_repo(tmp_path)
    write(repo / "facts" / "queues.md", fact("queues", bullet("depth caps at 10k", tag="limits")))

    result = layout.migrate_legacy_facts(repo)

    assert result.moved == [f"facts/queues.md -> {CANON}/queues.md"]
    assert (repo / CANON / "queues.md").is_file()
    assert not (repo / "facts").exists()


def test_a_topic_both_layouts_carry_is_merged_not_overwritten(tmp_path):
    repo = make_repo(tmp_path)
    shared = bullet("the lb keeps stale targets")
    only_legacy = bullet("blue/green needs a 90 second drain", tag="drain")
    write(repo / CANON / "deploys.md", fact("deploys", shared))
    write(repo / "facts" / "deploys.md", fact("deploys", shared, only_legacy))
    commit(repo, "both layouts carry deploys")

    result = layout.migrate_legacy_facts(repo)

    assert result.moved == []
    assert result.merged == [f"facts/deploys.md merged into {CANON}/deploys.md (1 bullets)"]
    text = (repo / CANON / "deploys.md").read_text(encoding="utf-8")
    assert text.count("the lb keeps stale targets") == 1  # canonical wins, no duplicate
    assert text.count("90 second drain") == 1  # the bullet only the legacy file had
    assert text.splitlines()[-1] == only_legacy
    assert not (repo / "facts").exists()
    assert gitops.git(repo, "ls-files", "--", "facts/deploys.md") == ""


def test_a_restamped_retagged_copy_of_a_canonical_bullet_is_not_duplicated(tmp_path):
    """Identity is the sentence: the same fact rendered differently is still the same fact."""
    repo = make_repo(tmp_path)
    sentence = "the lb keeps stale targets"
    write(repo / CANON / "deploys.md", fact("deploys", bullet(sentence)))
    write(
        repo / "facts" / "deploys.md",
        fact("deploys", bullet(sentence, tag="lb", category="constraint", date="2026-01-01")),
    )

    result = layout.migrate_legacy_facts(repo)

    text = (repo / CANON / "deploys.md").read_text(encoding="utf-8")
    assert text.count(sentence) == 1
    assert result.merged == [f"facts/deploys.md merged into {CANON}/deploys.md (0 bullets)"]


def test_two_sentences_sharing_a_topic_key_are_both_kept(tmp_path):
    """A topic key is the first six words — dropping the loser would delete knowledge.

    `harvest.apply_batch` runs no preservation gate, so a key-only dedup would lose the
    legacy sentence silently and permanently. Both travel; the reviewer reconciles them.
    """
    repo = make_repo(tmp_path)
    canonical = bullet("the lb keeps stale targets for 30 seconds")
    legacy = bullet("the lb keeps stale targets for 60 seconds")
    assert (
        units.parse_bullet_line(canonical, 1).topic_key
        == units.parse_bullet_line(legacy, 1).topic_key
    )
    write(repo / CANON / "deploys.md", fact("deploys", canonical))
    write(repo / "facts" / "deploys.md", fact("deploys", legacy))

    result = layout.migrate_legacy_facts(repo)

    text = (repo / CANON / "deploys.md").read_text(encoding="utf-8")
    assert "for 30 seconds" in text and "for 60 seconds" in text
    assert result.merged[0] == f"facts/deploys.md merged into {CANON}/deploys.md (1 bullets)"
    assert "share a topic key" in result.merged[1]


def test_a_legacy_line_no_parser_can_key_is_carried_over_verbatim(tmp_path):
    """Never dropped: an unparseable bullet, and prose, travel as-is into the canonical file."""
    repo = make_repo(tmp_path)
    broken = "- [gotcha]"
    prose = "note: the drain timeout was raised twice in 2026"
    write(repo / CANON / "deploys.md", fact("deploys", bullet("the lb keeps stale targets")))
    write(repo / "facts" / "deploys.md", fact("deploys") + broken + "\n" + prose + "\n")

    result = layout.migrate_legacy_facts(repo)

    text = (repo / CANON / "deploys.md").read_text(encoding="utf-8")
    assert broken in text
    assert prose in text
    assert result.merged[0] == f"facts/deploys.md merged into {CANON}/deploys.md (0 bullets)"
    assert any("verbatim" in line for line in result.merged[1:])
    assert not (repo / "facts").exists()


def test_a_placeholder_only_legacy_dir_is_simply_removed(tmp_path):
    repo = make_repo(tmp_path)
    write(repo / "facts" / ".gitkeep", "")
    commit(repo, "legacy placeholder")

    result = layout.migrate_legacy_facts(repo)

    assert (result.moved, result.merged, result.removed_dir) == ([], [], True)
    assert not (repo / "facts").exists()
    assert not (repo / CANON / ".gitkeep").exists()
    assert gitops.git(repo, "ls-files", "--", "facts/.gitkeep") == ""


def test_subdirectories_and_other_files_move_as_is(tmp_path):
    repo = make_repo(tmp_path)
    write(repo / "facts" / "archive" / "old.md", fact("old", bullet("an archived note")))
    write(repo / "facts" / "notes.txt", "loose note\n")
    commit(repo, "legacy extras")

    result = layout.migrate_legacy_facts(repo)

    assert result.moved == [
        f"facts/archive -> {CANON}/archive",
        f"facts/notes.txt -> {CANON}/notes.txt",
    ]
    assert "an archived note" in (repo / CANON / "archive" / "old.md").read_text(encoding="utf-8")
    assert (repo / CANON / "notes.txt").read_text(encoding="utf-8") == "loose note\n"
    assert not (repo / "facts").exists()


def test_no_legacy_directory_is_a_no_op(tmp_path):
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "deploys.md", fact("deploys", bullet("the lb keeps stale")))
    commit(repo, "canonical only")
    before = canonical.read_bytes()

    result = layout.migrate_legacy_facts(repo)

    assert (result.moved, result.merged, result.removed_dir) == ([], [], False)
    assert canonical.read_bytes() == before
    assert not (repo / "facts").exists()
    assert gitops.is_clean(repo)  # nothing written at all


def test_a_traversal_shaped_legacy_filename_stays_inside_the_canonical_dir(tmp_path):
    repo = make_repo(tmp_path)
    name = "..%2f..%2fescape.md"
    write(repo / "facts" / name, fact("escape", bullet("a fact under a hostile name")))

    result = layout.migrate_legacy_facts(repo)

    assert result.moved == [f"facts/{name} -> {CANON}/{name}"]
    assert (repo / CANON / name).is_file()
    assert not (repo / "escape.md").exists()
    assert not (tmp_path / "escape.md").exists()
    assert not (repo.parent / "escape.md").exists()


def test_a_glob_shaped_legacy_filename_cannot_take_its_siblings_with_it(tmp_path):
    """`git rm -- 'facts/a*b.md'` would delete the sibling too — before it was migrated."""
    repo = make_repo(tmp_path)
    write(repo / CANON / "a*b.md", fact("a-b", bullet("canonical bullet")))
    write(repo / "facts" / "a*b.md", fact("a-b", bullet("canonical bullet")))
    write(repo / "facts" / "aXb.md", fact("a-x-b", bullet("the sibling a glob would sweep")))
    commit(repo, "a glob-shaped legacy filename")

    layout.migrate_legacy_facts(repo)

    assert "the sibling a glob would sweep" in (repo / CANON / "aXb.md").read_text(
        encoding="utf-8"
    )
    assert not (repo / "facts").exists()


def test_a_canonical_entry_symlinked_out_of_the_repo_is_refused(tmp_path):
    """The containment proof is made on the RESOLVED destination, so a merge target that
    points out of the repo is refused before a single byte is appended through it."""
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("untouched\n", encoding="utf-8")
    (repo / CANON).mkdir(parents=True)
    try:
        (repo / CANON / "deploys.md").symlink_to(outside)
    except OSError:
        pytest.skip("filesystem does not support symlinks")
    write(repo / "facts" / "deploys.md", fact("deploys", bullet("a fact aimed at the symlink")))

    with pytest.raises(MnemeError) as exc:
        layout.migrate_legacy_facts(repo)

    assert "facts/deploys.md" in str(exc.value)
    assert outside.read_text(encoding="utf-8") == "untouched\n"
    assert (repo / "facts" / "deploys.md").is_file()


def test_a_crlf_bom_canonical_file_survives_a_merge_byte_for_byte(tmp_path):
    repo = make_repo(tmp_path)
    original = ("\ufeff" + fact("deploys", bullet("the lb keeps stale targets"))).replace(
        "\n", "\r\n"
    )
    dest = repo / CANON / "deploys.md"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(original.encode("utf-8"))
    added = bullet("blue/green needs a 90 second drain", tag="drain")
    write(repo / "facts" / "deploys.md", fact("deploys", added))

    layout.migrate_legacy_facts(repo)

    assert dest.read_bytes() == original.encode("utf-8") + (added + "\r\n").encode("utf-8")


def test_a_collision_that_cannot_be_merged_refuses_instead_of_overwriting(tmp_path):
    repo = make_repo(tmp_path)
    write(repo / CANON / "notes.txt", "canonical note\n")
    write(repo / "facts" / "notes.txt", "legacy note\n")

    with pytest.raises(MnemeError) as exc:
        layout.migrate_legacy_facts(repo)

    assert "notes.txt" in str(exc.value)
    assert (repo / CANON / "notes.txt").read_text(encoding="utf-8") == "canonical note\n"
    assert (repo / "facts" / "notes.txt").read_text(encoding="utf-8") == "legacy note\n"


def test_migration_neither_commits_nor_moves_the_branch(tmp_path):
    """Callers own the branch and the commit — PR-only lives one level up (spec §7.3)."""
    repo = make_repo(tmp_path)
    write(repo / "facts" / "deploys.md", fact("deploys", bullet("the lb keeps stale targets")))
    commit(repo, "add the deploys fact")
    gitops.create_branch(repo, "mneme/harvest-test")
    head = gitops.head_sha(repo)

    layout.migrate_legacy_facts(repo)

    assert gitops.head_sha(repo) == head
    assert gitops.current_branch(repo) == "mneme/harvest-test"
    assert not gitops.is_clean(repo)  # staged, waiting for the caller's commit
