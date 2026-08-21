"""`layout.migrate_legacy_facts` — a legacy top-level `facts/` becomes the canonical dir.

The migration is the one operation that touches every fact a pre-0.5 repo owns at once,
so these tests are written around the two ways it could go wrong: losing knowledge (a
bullet dropped in a merge, a frontmatter key dropped, a file deleted rather than moved, a
subdirectory left behind) and losing containment (a legacy filename — or a symlink at
either end of the move — is repo content, which a contributor or a merged PR can commit).
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
    # A knowledge repo is a plugin, so its canonical facts live under the router skill.
    # `units.facts_write_dir` reads the manifest to decide that; without one this fixture
    # would be a plain repo and migrate into `mneme-index/` instead.
    (repo / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (repo / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "kb", "version": "0.1.0"}\n', encoding="utf-8"
    )
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
    assert result.merged[0] == f"facts/deploys.md merged into {CANON}/deploys.md (1 bullets)"
    text = (repo / CANON / "deploys.md").read_text(encoding="utf-8")
    assert text.count("the lb keeps stale targets") == 1  # canonical wins, no duplicate
    assert text.count("90 second drain") == 1  # the bullet only the legacy file had
    assert text.splitlines()[-1] == only_legacy
    assert not (repo / "facts").exists()
    assert gitops.git(repo, "ls-files", "--", "facts/deploys.md") == ""


def test_a_restamped_retagged_copy_of_a_canonical_bullet_merges_and_the_note_names_it(tmp_path):
    """The commonest overlap a pre-0.5 repo has: one sentence, re-verified or re-tagged.

    Both files share a stem, so both renderings share a unit id (`facts/<stem>#<key>`), and
    `index_tree` stores the first and reports the second as a duplicate. The legacy
    rendering was therefore never retrievable while the two files coexisted, and folding it
    away takes nothing from a reader — an earlier round refused this merge to protect a row
    the index did not have, which turned two thirds of ordinary collisions into `-legacy`
    asides and put two rows with the SAME topic in the routing table.

    What the legacy line still has is a human reader, so the note writes its rendering out
    rather than tallying it: `2026-01-01` and `#lb` are in the pull request even though the
    canonical line won.
    """
    repo = make_repo(tmp_path)
    sentence = "the lb keeps stale targets"
    write(repo / CANON / "deploys.md", fact("deploys", bullet(sentence)))
    write(
        repo / "facts" / "deploys.md",
        fact("deploys", bullet(sentence, tag="lb", category="constraint", date="2026-01-01")),
    )

    result = layout.migrate_legacy_facts(repo)

    assert not (repo / CANON / "deploys-legacy.md").exists()  # merged, not kept apart
    assert not (repo / "facts").exists()
    canonical = (repo / CANON / "deploys.md").read_text(encoding="utf-8")
    assert "[gotcha]" in canonical and "#deploy" in canonical
    note = " ".join(result.merged)
    assert "[constraint]" in note and "#lb" in note and "2026-01-01" in note


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


def symlink(link, target):
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("filesystem does not support symlinks")


def test_a_legacy_directory_that_is_a_symlink_is_refused(tmp_path):
    """The back-compat shim: `facts` -> the canonical dir, which reads correctly today.

    Followed, `iterdir` yields the CANONICAL files through the link, every one of them
    "merges" into itself (0 bullets carried), and `git ls-files` — which does not traverse
    a symlinked directory — reports each as untracked, so the removal falls through to
    `unlink` and deletes the real fact. Refused before the first read instead.
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "deploys.md", fact("deploys", bullet("the lb keeps stale targets")))
    write(repo / CANON / "queues.md", fact("queues", bullet("depth caps at 10k", tag="limits")))
    commit(repo, "canonical facts")
    symlink(repo / "facts", repo / CANON)

    with pytest.raises(MnemeError) as exc:
        layout.migrate_legacy_facts(repo)

    assert "symlink" in str(exc.value)
    assert sorted(p.name for p in (repo / CANON).iterdir()) == ["deploys.md", "queues.md"]
    assert "the lb keeps stale targets" in (repo / CANON / "deploys.md").read_text(
        encoding="utf-8"
    )
    # The symlink itself is the only thing git sees: the migration wrote nothing.
    assert gitops.git(repo, "status", "--porcelain").split() == ["??", "facts"]


def test_a_legacy_directory_symlinked_outside_the_repo_is_refused(tmp_path):
    """The unrecoverable one: the files are not in the repo, so no rollback can restore
    them once this migration has renamed or unlinked them."""
    repo = make_repo(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    write(outside / "deploys.md", fact("deploys", bullet("a fact that lives outside")))
    symlink(repo / "facts", outside)

    with pytest.raises(MnemeError) as exc:
        layout.migrate_legacy_facts(repo)

    assert "symlink" in str(exc.value)
    assert (outside / "deploys.md").is_file()
    assert not (repo / CANON).exists()
    assert gitops.git(repo, "status", "--porcelain").split() == ["??", "facts"]


def test_a_legacy_entry_symlinked_out_of_the_repo_is_refused(tmp_path):
    """Same hazard one level down: `_remove` would unlink the file at the far end."""
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text(fact("deploys", bullet("a fact that lives outside")), encoding="utf-8")
    write(repo / CANON / "deploys.md", fact("deploys", bullet("the lb keeps stale targets")))
    (repo / "facts").mkdir()
    symlink(repo / "facts" / "deploys.md", outside)

    with pytest.raises(MnemeError) as exc:
        layout.migrate_legacy_facts(repo)

    assert "facts/deploys.md" in str(exc.value) and "symlink" in str(exc.value)
    assert outside.read_text(encoding="utf-8") == fact(
        "deploys", bullet("a fact that lives outside")
    )
    assert (repo / CANON / "deploys.md").read_text(encoding="utf-8") == fact(
        "deploys", bullet("the lb keeps stale targets")
    )


def test_a_canonical_facts_dir_symlinked_out_of_the_repo_is_refused(tmp_path):
    """The mirror of the legacy-directory shim, and the worse half of it.

    Followed, every fact is renamed to the far end of the link, `facts/` is deleted, and
    the result still says "moved": the caller's `git add -A` then stages a bare deletion of
    knowledge with no counterpart anywhere in the tree. Resolving the destination is no
    defence — the base resolves to the far end too, so containment can never fail.
    """
    repo = make_repo(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (repo / CANON).parent.mkdir(parents=True)
    symlink(repo / CANON, outside)
    write(repo / "facts" / "deploys.md", fact("deploys", bullet("the lb keeps stale targets")))
    commit(repo, "a canonical facts dir that is a link out of the repo")

    with pytest.raises(MnemeError) as exc:
        layout.migrate_legacy_facts(repo)

    assert "symlink" in str(exc.value) and CANON in str(exc.value)
    assert (repo / "facts" / "deploys.md").is_file()  # nothing moved, nothing deleted
    assert sorted(p.name for p in outside.iterdir()) == []
    assert gitops.is_clean(repo)


def test_a_symlinked_segment_above_the_canonical_dir_is_refused(tmp_path):
    """One level up is the same hazard: `skills/knowledge-index` can be the link."""
    repo = make_repo(tmp_path)
    outside = tmp_path / "elsewhere"
    (outside / "facts").mkdir(parents=True)
    (repo / "skills").mkdir()
    symlink(repo / "skills" / "knowledge-index", outside)
    write(repo / "facts" / "deploys.md", fact("deploys", bullet("the lb keeps stale targets")))

    with pytest.raises(MnemeError) as exc:
        layout.migrate_legacy_facts(repo)

    assert "symlink" in str(exc.value) and "skills/knowledge-index" in str(exc.value)
    assert (repo / "facts" / "deploys.md").is_file()
    assert sorted(p.name for p in (outside / "facts").iterdir()) == []


def test_a_subdirectory_both_layouts_carry_is_merged_entry_by_entry(tmp_path):
    """Two directories of the same name are not a collision between the facts inside them.

    The code this migration replaces walked the legacy tree file-by-file and merged such a
    directory cleanly; refusing it here would strand a repo shape that migrates today on a
    hard error with nothing actually in conflict.
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "archive" / "kept.md", fact("kept", bullet("a canonical archived note")))
    write(repo / "facts" / "archive" / "old.md", fact("old", bullet("a legacy archived note")))
    write(repo / "facts" / "archive" / "deep" / "older.md", fact("older", bullet("deeper still")))
    commit(repo, "both layouts carry an archive dir")

    result = layout.migrate_legacy_facts(repo)

    assert result.moved == [
        f"facts/archive/deep -> {CANON}/archive/deep",
        f"facts/archive/old.md -> {CANON}/archive/old.md",
    ]
    assert "a canonical archived note" in (repo / CANON / "archive" / "kept.md").read_text(
        encoding="utf-8"
    )
    assert "a legacy archived note" in (repo / CANON / "archive" / "old.md").read_text(
        encoding="utf-8"
    )
    assert "deeper still" in (repo / CANON / "archive" / "deep" / "older.md").read_text(
        encoding="utf-8"
    )
    assert not (repo / "facts").exists()


def test_a_file_colliding_inside_a_shared_subdirectory_still_refuses(tmp_path):
    """Merging the directories does not weaken the file-level gate one level down."""
    repo = make_repo(tmp_path)
    write(repo / CANON / "archive" / "notes.txt", "canonical note\n")
    write(repo / "facts" / "archive" / "notes.txt", "legacy note\n")

    with pytest.raises(MnemeError) as exc:
        layout.migrate_legacy_facts(repo)

    assert "facts/archive/notes.txt" in str(exc.value)
    canonical = repo / CANON / "archive" / "notes.txt"
    assert canonical.read_text(encoding="utf-8") == "canonical note\n"
    legacy = repo / "facts" / "archive" / "notes.txt"
    assert legacy.read_text(encoding="utf-8") == "legacy note\n"


def test_an_md_file_inside_a_shared_subdirectory_is_merged(tmp_path):
    repo = make_repo(tmp_path)
    shared = bullet("the lb keeps stale targets")
    only_legacy = bullet("blue/green needs a 90 second drain", tag="drain")
    write(repo / CANON / "archive" / "deploys.md", fact("deploys", shared))
    write(repo / "facts" / "archive" / "deploys.md", fact("deploys", shared, only_legacy))

    result = layout.migrate_legacy_facts(repo)

    assert (
        result.merged[0]
        == f"facts/archive/deploys.md merged into {CANON}/archive/deploys.md (1 bullets)"
    )
    text = (repo / CANON / "archive" / "deploys.md").read_text(encoding="utf-8")
    assert text.count("90 second drain") == 1
    assert not (repo / "facts").exists()


def test_a_canonical_file_with_no_frontmatter_gains_a_block_from_the_legacy_header(tmp_path):
    """The legacy header must not end up as loose prose in the merged file.

    `topic:`/`owner:` dropped into the body is nothing lost but everything demoted — a file
    structurally worse than the well-formed one the merge just consumed. A block is created
    instead (an insert; not one existing line is rewritten), so the result parses.
    """
    repo = make_repo(tmp_path)
    canonical_bullet = bullet("canonical only bullet")
    legacy_bullet = bullet("legacy only bullet", tag="drain")
    write(repo / CANON / "deploys.md", canonical_bullet + "\n")  # no frontmatter at all
    write(
        repo / "facts" / "deploys.md",
        "---\ntopic: deploys\nowner: sre\n---\n" + legacy_bullet + "\n",
    )

    result = layout.migrate_legacy_facts(repo)

    text = (repo / CANON / "deploys.md").read_text(encoding="utf-8")
    meta, body = units.parse_frontmatter(text)
    assert meta == {"topic": "deploys", "owner": "sre"}
    assert body.splitlines() == [canonical_bullet, legacy_bullet]
    assert result.merged[0] == f"facts/deploys.md merged into {CANON}/deploys.md (1 bullets)"
    assert "had no frontmatter" in result.merged[1]
    assert not (repo / "facts").exists()


def test_readable_legacy_metadata_is_not_buried_in_an_unterminated_canonical_file(tmp_path):
    """Metadata is knowledge too, and the same refusal protects it.

    This fixture used to assert that `owner: sre` had ARRIVED in the canonical file — a
    buried-bytes assertion: the destination's header is unterminated, so the reader yields
    nothing from it, and appending the legacy keys there while deleting the file that held
    them made three retrievable keys unretrievable while the report said "merged". The
    merge is refused instead, and the check is the one the sibling module uses: the saved
    file parses, nothing was dropped, and no key that was readable before is gone.
    """
    repo = make_repo(tmp_path)
    canonical_text = "---\ntopic: deploys\n" + bullet("canonical bullet") + "\n"
    write(repo / CANON / "deploys.md", canonical_text)
    legacy_text = "---\ntopic: deploys\nowner: sre\nsources: incident-4412\n---\n"
    write(repo / "facts" / "deploys.md", legacy_text)
    before, _body = units.parse_frontmatter(legacy_text)

    result = layout.migrate_legacy_facts(repo)

    aside = repo / CANON / "deploys-legacy.md"
    assert aside.is_file(), "the readable legacy file must be kept, not buried"
    after, _body = units.parse_frontmatter(aside.read_text(encoding="utf-8"))  # parses
    assert set(before) <= set(after)  # every key that was readable still is
    assert after["owner"] == "sre" and after["sources"] == "incident-4412"
    # The unreadable destination is left exactly as it was — nothing appended into it.
    assert (repo / CANON / "deploys.md").read_text(encoding="utf-8") == canonical_text
    assert any("kept separate" in line for line in result.moved)
    assert not (repo / "facts").exists()


def test_a_canonical_file_that_is_empty_gains_the_legacy_header_and_bullets(tmp_path):
    repo = make_repo(tmp_path)
    legacy_bullet = bullet("legacy only bullet", tag="drain")
    write(repo / CANON / "deploys.md", "")
    write(repo / "facts" / "deploys.md", "---\ntopic: deploys\n---\n" + legacy_bullet + "\n")

    layout.migrate_legacy_facts(repo)

    text = (repo / CANON / "deploys.md").read_text(encoding="utf-8")
    assert text == "---\ntopic: deploys\n---\n" + legacy_bullet + "\n"


def test_a_regular_file_where_the_canonical_dir_belongs_is_a_mneme_error(tmp_path):
    """A repo-shape problem, not a bug: it must abort the caller's flow through the
    guarded path with a message naming the file, not as a raw FileExistsError."""
    repo = make_repo(tmp_path)
    (repo / CANON).parent.mkdir(parents=True)
    (repo / CANON).write_text("not a directory\n", encoding="utf-8")
    write(repo / "facts" / "deploys.md", fact("deploys", bullet("the lb keeps stale targets")))

    with pytest.raises(MnemeError) as exc:
        layout.migrate_legacy_facts(repo)

    assert "facts/deploys.md" in str(exc.value) and CANON in str(exc.value)
    assert (repo / "facts" / "deploys.md").is_file()  # nothing lost


def test_a_carried_line_with_an_exotic_separator_is_not_split_in_two(tmp_path):
    """`str.splitlines` breaks on \\u2028 and friends; inside a bullet those are data.

    Split there, the canonical file gains a truncated bullet plus a stray fragment, and the
    separator byte is deleted — a silent edit to a line the merge promised to move verbatim.
    """
    repo = make_repo(tmp_path)
    exotic = bullet("alpha\u2028beta gamma delta", tag="drain")
    write(repo / CANON / "deploys.md", fact("deploys", bullet("the lb keeps stale targets")))
    write(repo / "facts" / "deploys.md", fact("deploys", exotic))

    result = layout.migrate_legacy_facts(repo)

    text = (repo / CANON / "deploys.md").read_text(encoding="utf-8")
    assert text.split("\n")[-2] == exotic  # one line, separator intact
    assert result.merged[0] == f"facts/deploys.md merged into {CANON}/deploys.md (1 bullets)"


def test_legacy_frontmatter_keys_the_canonical_file_lacks_are_carried_over(tmp_path):
    """The legacy file is deleted at the end of the merge: a key only it carries has to
    land in the canonical header, or it is knowledge the migration threw away."""
    repo = make_repo(tmp_path)
    shared = bullet("the lb keeps stale targets")
    write(repo / CANON / "deploys.md", fact("deploys", shared))
    write(
        repo / "facts" / "deploys.md",
        "---\ntopic: deploys\nowner: platform-team\nsources: incident-4412\n---\n"
        + shared + "\n",
    )

    result = layout.migrate_legacy_facts(repo)

    text = (repo / CANON / "deploys.md").read_text(encoding="utf-8")
    meta, body = units.parse_frontmatter(text)
    assert meta == {"topic": "deploys", "owner": "platform-team", "sources": "incident-4412"}
    assert body.strip() == shared  # the body is untouched: the bullet was already there
    assert result.merged[0] == f"facts/deploys.md merged into {CANON}/deploys.md (0 bullets)"
    assert "frontmatter key(s) carried over: owner, sources" in " ".join(result.merged)
    assert not (repo / "facts").exists()


def test_a_frontmatter_key_the_two_files_disagree_on_is_reported_not_resolved(tmp_path):
    repo = make_repo(tmp_path)
    shared = bullet("the lb keeps stale targets")
    write(repo / CANON / "deploys.md", "---\ntopic: deploys\nowner: platform\n---\n" + shared + "\n")
    write(repo / "facts" / "deploys.md", "---\ntopic: deploys\nowner: sre\n---\n" + shared + "\n")

    result = layout.migrate_legacy_facts(repo)

    # One file has one header, so the two values cannot both stay keys. The canonical one
    # wins the header and the legacy line is DEMOTED into the body rather than discarded —
    # sound because `topic` is the only fact-file key any reader projects into a row, an
    # FTS column or the classify bundle (`build._fact_rows`, `scaffold`, `classify`), so
    # `owner:` is content a human reads and it reads the same three lines lower. The note
    # names both values, which is the whole of "reported rather than resolved".
    merged = (repo / CANON / "deploys.md").read_text(encoding="utf-8")
    canonical_meta, body = units.parse_frontmatter(merged)
    assert canonical_meta["owner"] == "platform"
    assert "owner: sre" in body
    note = " ".join(result.merged)
    assert "owner: platform (kept)" in note and "owner: sre" in note
    assert not (repo / CANON / "deploys-legacy.md").exists()


def test_an_unterminated_canonical_frontmatter_does_not_wedge_the_merge(tmp_path):
    """`harvest._body_start` raises here, naming no file — right for one fact apply, wrong
    for a migration wired into every branch flow. Lint (MN010) reports that file by name.

    The migration must not wedge — and must not "succeed" by folding readable facts into a
    file no reader can parse, which is what it used to do: this test asserted only that the
    legacy bullet's BYTES had arrived, while the index yielded zero rows for that file. The
    legacy file is kept beside the broken one instead, where its facts stay retrievable.
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "deploys.md", "---\ntopic: deploys\n" + bullet("the lb keeps stale") + "\n")
    only_legacy = bullet("blue/green needs a 90 second drain", tag="drain")
    write(repo / "facts" / "deploys.md", fact("deploys", only_legacy))

    result = layout.migrate_legacy_facts(repo)

    aside = repo / CANON / "deploys-legacy.md"
    assert aside.is_file()
    assert only_legacy in aside.read_text(encoding="utf-8")
    units.parse_frontmatter(aside.read_text(encoding="utf-8"))  # still readable
    # The broken file is left exactly as it was — nothing buried in it, nothing rewritten.
    assert (repo / CANON / "deploys.md").read_text(encoding="utf-8") == (
        "---\ntopic: deploys\n" + bullet("the lb keeps stale") + "\n"
    )
    assert any("kept separate" in line for line in result.moved)
    assert not (repo / "facts").exists()


def test_a_carried_frontmatter_key_keeps_the_canonical_files_crlf_and_bom(tmp_path):
    repo = make_repo(tmp_path)
    original = "\ufeff---\r\ntopic: deploys\r\n---\r\n" + bullet("the lb keeps stale") + "\r\n"
    dest = repo / CANON / "deploys.md"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(original.encode("utf-8"))
    write(
        repo / "facts" / "deploys.md",
        "---\ntopic: deploys\nowner: platform-team\n---\n" + bullet("the lb keeps stale") + "\n",
    )

    layout.migrate_legacy_facts(repo)

    assert dest.read_bytes() == (
        "\ufeff---\r\ntopic: deploys\r\nowner: platform-team\r\n---\r\n"
        + bullet("the lb keeps stale") + "\r\n"
    ).encode("utf-8")


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
