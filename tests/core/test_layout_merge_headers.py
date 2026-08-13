"""A merge may never make a canonical fact file less readable than it was.

`layout._merge` folds a legacy fact file into a canonical one and then deletes the legacy
file, so every line it does not carry is gone, and every line it carries into the wrong
place can break the file for the reader. The failure that matters is not an exception —
it is a file whose bytes are all present and which `units.parse_frontmatter` then rejects,
because every consumer that walks facts (lint, the index build, search, the classify
bundle) reads through that parser. A bullet that was retrievable before the migration and
is not after it has been lost, whatever the bytes say.

Two rounds of fixes here failed by re-deriving the reader's grammar instead of asking it:
`_meta_blocks` recognises a key only at the start of a line and attaches every other line
to the PRECEDING key, so "unkeyable" lines were caught only in first position, and a stray
line one row lower still entered the header. These tests therefore assert the PROPERTY
over a table of header shapes rather than pinning the two strings that were reported:

  1. the merged file parses,
  2. every legacy line survives somewhere in it,
  3. the index yields no fewer fact rows than before the merge.
"""
import subprocess

import pytest

from mneme_core import layout, units
from mneme_core.errors import MnemeError
from mneme_index import build, db

CANON = units.FACTS_CANONICAL

CANONICAL_BULLET = "- [gotcha] Canonical bullet that was already retrievable #x (verified: 2026-08-12)\n"
LEGACY_BULLET = "- [constraint] Legacy bullet arriving in the merge #y (verified: 2026-08-12)\n"

# Every header shape the reviewer demonstrated, in both orders where order mattered.
MALFORMED_HEADERS = {
    "stray-line-first": "not a key line\ntopic: t\n",
    "stray-line-after-a-key": "topic: t\nnot a key line\n",
    "indented-line-first": "  indented: yes\ntopic: t\n",
    "indented-line-after-a-key": "topic: t\n  indented: yes\n",
    "flush-left-list": "tags:\n- a\n- b\n",
    "prose-tail": "topic: deploys\nowner: platform\nOwned by the platform team since 2024\n",
    "bad-nested-block": "owner:\n  not a nested key\n",
    "tab-continuation": "topic: t\n\tsub: v\n",
    "nothing-keyable-at-all": "not a key line\nalso not one\n",
}

WELL_FORMED_HEADER = "topic: t\nowner: platform\n"


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


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def fact_rows(tmp_path, repo, name):
    """How many fact bullets the index can actually retrieve from `repo` right now."""
    conn = db.open_db(tmp_path / f"{name}.db")
    try:
        build.index_tree(conn, name, repo)
        return conn.execute(
            "SELECT COUNT(*) AS n FROM units WHERE kind = 'fact'"
        ).fetchone()["n"]
    finally:
        conn.close()


def topic_rows(tmp_path, repo, name):
    """The (topic, sentence) pairs the index can retrieve — the `name` column included.

    `fact_rows` counts, which cannot see a fact that survived under the WRONG topic:
    `list_facts(topic=…)` and the router's table both filter on that column, so a bullet
    whose topic changed has stopped being reachable the way an agent reaches it.
    """
    conn = db.open_db(tmp_path / f"{name}.db")
    try:
        build.index_tree(conn, "p", repo)
        return {
            (r["name"], r["description"])
            for r in conn.execute("SELECT * FROM units WHERE kind = 'fact'")
        }
    finally:
        conn.close()


def assert_merge_preserved(tmp_path, repo, legacy_header, canonical_path):
    """The three properties, checked around a real migration."""
    before = fact_rows(tmp_path, repo, "before")
    legacy_lines = [l for l in legacy_header.splitlines() if l.strip()]

    layout.migrate_legacy_facts(repo)

    merged = canonical_path.read_text(encoding="utf-8")
    units.parse_frontmatter(merged)  # 1. the reader accepts what the merge produced
    for line in legacy_lines:  # 2. nothing the legacy file carried was dropped
        assert line.strip() in merged, f"legacy header line vanished: {line!r}"
    assert "Legacy bullet arriving in the merge" in merged
    after = fact_rows(tmp_path, repo, "after")
    assert after >= before, f"retrievable facts fell from {before} to {after}"
    assert after >= 1
    return merged


@pytest.mark.parametrize("shape", sorted(MALFORMED_HEADERS))
def test_a_malformed_legacy_header_never_breaks_a_canonical_file_without_one(tmp_path, shape):
    """The `new_block` branch: the canonical file has no header, so one is created."""
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", CANONICAL_BULLET)
    write(repo / "facts" / "t.md", f"---\n{MALFORMED_HEADERS[shape]}---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    assert_merge_preserved(tmp_path, repo, MALFORMED_HEADERS[shape], canonical)


@pytest.mark.parametrize("shape", sorted(MALFORMED_HEADERS))
def test_a_malformed_legacy_header_never_breaks_a_canonical_file_with_one(tmp_path, shape):
    """The `_carry_meta` branch: the canonical file already has a header to carry keys into."""
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", "---\nsummary: canonical\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "t.md", f"---\n{MALFORMED_HEADERS[shape]}---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    merged = assert_merge_preserved(tmp_path, repo, MALFORMED_HEADERS[shape], canonical)
    meta, _body = units.parse_frontmatter(merged)
    assert meta.get("summary") == "canonical"  # the canonical header still reads


def test_a_well_formed_legacy_header_still_becomes_the_block(tmp_path):
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", CANONICAL_BULLET)
    write(repo / "facts" / "t.md", f"---\n{WELL_FORMED_HEADER}---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    merged = assert_merge_preserved(tmp_path, repo, WELL_FORMED_HEADER, canonical)
    meta, _body = units.parse_frontmatter(merged)
    assert meta.get("topic") == "t"
    assert meta.get("owner") == "platform"  # carried as metadata, not demoted to prose


def test_a_well_formed_legacy_key_still_reaches_an_existing_canonical_header(tmp_path):
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", "---\ntopic: t\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "t.md", "---\ntopic: t\nowner: platform\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    layout.migrate_legacy_facts(repo)

    meta, _body = units.parse_frontmatter(canonical.read_text(encoding="utf-8"))
    assert meta.get("owner") == "platform"


def test_carried_body_is_never_read_as_a_frontmatter_block(tmp_path):
    """A legacy body opening with `---` must not become a header when it lands first.

    The legacy file's own leading blank line is what kept those lines out of its header;
    dropping it while carrying them into an empty canonical file turned prose into an
    unterminated block, and the delimiter-deduping dropped the closing `---` besides.
    """
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", "")
    write(repo / "facts" / "t.md", "\n---\ntopic: x\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")
    before = fact_rows(tmp_path, repo, "before")

    layout.migrate_legacy_facts(repo)

    merged = canonical.read_text(encoding="utf-8")
    units.parse_frontmatter(merged)  # must not raise
    assert merged.count("---") >= 2, "a delimiter the legacy file carried was deleted"
    assert "Legacy bullet arriving in the merge" in merged
    assert fact_rows(tmp_path, repo, "after") >= max(before, 1)


# The canonical side, which the first table never varied — and which is where the merge
# could bury facts with a perfectly well-formed legacy file, or none at all.
BROKEN_CANONICAL_HEADERS = {
    "unterminated": "---\ntopic: t\n",
    "terminated-but-malformed": "---\nnot a key line\n---\n",
    "bad-nested-block": "---\nowner:\n  not a nested key\n---\n",
}

LEGACY_SHAPES = {
    "well-formed-header": "---\ntopic: t\n---\n",
    "no-header-at-all": "",
}


@pytest.mark.parametrize("legacy_shape", sorted(LEGACY_SHAPES))
@pytest.mark.parametrize("canonical_shape", sorted(BROKEN_CANONICAL_HEADERS))
def test_facts_are_never_buried_in_a_canonical_file_that_does_not_parse(
    tmp_path, canonical_shape, legacy_shape
):
    """The merge is a convenience; keeping every fact readable is not.

    A canonical file whose own header the reader rejects yields nothing, so bullets folded
    into it stop being retrievable — with a well-formed legacy file, or one with no header
    at all, which is why no guard on the legacy side can catch this. The legacy file is
    kept beside it instead.
    """
    repo = make_repo(tmp_path)
    canonical = write(
        repo / CANON / "t.md", BROKEN_CANONICAL_HEADERS[canonical_shape] + CANONICAL_BULLET
    )
    canonical_bytes = canonical.read_bytes()
    write(repo / "facts" / "t.md", LEGACY_SHAPES[legacy_shape] + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")
    before = fact_rows(tmp_path, repo, "before")

    result = layout.migrate_legacy_facts(repo)

    assert fact_rows(tmp_path, repo, "after") >= before
    assert canonical.read_bytes() == canonical_bytes  # the broken file is left untouched
    aside = repo / CANON / "t.legacy.md"
    assert aside.is_file(), "the legacy file must be kept, not buried"
    assert "Legacy bullet arriving in the merge" in aside.read_text(encoding="utf-8")
    assert not (repo / "facts").exists()
    assert any("kept separate" in line for line in result.moved)


def test_a_colliding_frontmatter_key_never_takes_extra_lines_with_it(tmp_path):
    """`_meta_blocks` glues an unrecognised line to the PRECEDING key, so discarding a
    colliding key's block deleted those lines from a file the merge then removes."""
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", "---\ntags: x\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "t.md", "---\ntags: x\n- a\n- b\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    layout.migrate_legacy_facts(repo)

    merged = canonical.read_text(encoding="utf-8")
    units.parse_frontmatter(merged)
    assert "- a" in merged and "- b" in merged  # glued lines survive somewhere
    assert "Legacy bullet arriving in the merge" in merged


def test_a_closing_code_fence_is_not_deleted_as_a_duplicate(tmp_path):
    """The dedup that dropped a `---` dropped a fence for the same reason; both are
    structure, and neither is the module's to delete."""
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", "---\ntopic: t\n---\n" + CANONICAL_BULLET)
    write(
        repo / "facts" / "t.md",
        "---\ntopic: t\n---\n" + LEGACY_BULLET + "```\ncode\n```\n",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    layout.migrate_legacy_facts(repo)

    merged = canonical.read_text(encoding="utf-8")
    assert merged.count("```") == 2, "the closing fence was deleted as a duplicate"


def test_neither_of_two_differing_frontmatter_values_is_discarded(tmp_path):
    """A colliding key with differing values keeps both VALUES — one keyed, one demoted.

    `topic` is the only fact-file key any reader projects into a row, so a differing
    `owner:` costs a reader nothing once its line is still in the file, and the note names
    both. Refusing the merge over it instead (an earlier round) protected a value nothing
    was retrieving at the price of a duplicate topic file.
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", "---\ntopic: t\nowner: platform\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "t.md", "---\ntopic: t\nowner: sre-oncall-team\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")
    before = fact_rows(tmp_path, repo, "before")

    result = layout.migrate_legacy_facts(repo)

    merged = (repo / CANON / "t.md").read_text(encoding="utf-8")
    canonical_meta, body = units.parse_frontmatter(merged)
    assert canonical_meta["owner"] == "platform"
    assert "owner: sre-oncall-team" in body
    assert fact_rows(tmp_path, repo, "after") >= before
    assert not (repo / CANON / "t.legacy.md").exists()
    assert "owner: sre-oncall-team" in " ".join(result.merged)


def test_an_ordinary_pre_0_5_collision_merges_rather_than_piling_up_asides(tmp_path):
    """The migration's own function, measured: refusing loses nothing and achieves nothing.

    A guard that treated every metadata value and every bullet rendering as retrievable
    declined 64% of realistic legacy/canonical pairs, leaving `<stem>.legacy.md` beside
    `<stem>.md` — two rows with the SAME topic in the routing table for the same sentence,
    where the duplicate-unit-id rule had shown it once. This is that measurement, shrunk to
    the shapes a pre-0.5 repo really carries. A merge is the expected outcome unless the
    two files declare genuinely DIFFERENT topics, which only a human can reconcile.
    """
    headers = {
        "mneme-written": "---\ntopic: t\n---\n",
        "plus-owner": "---\ntopic: t\nowner: platform\n---\n",
        "other-owner": "---\ntopic: t\nowner: sre\n---\n",
        "no-header": "",
    }
    bodies = {
        "same": CANONICAL_BULLET,
        "restamped": CANONICAL_BULLET.replace("2026-08-12", "2026-01-01"),
        "retagged": CANONICAL_BULLET.replace("#x", "#z"),
        "disjoint": LEGACY_BULLET,
    }
    asides = []
    for hc, canonical_header in headers.items():
        for hl, legacy_header in headers.items():
            for bc, canonical_body in bodies.items():
                for bl, legacy_body in bodies.items():
                    repo = make_repo(tmp_path / f"{hc}-{hl}-{bc}-{bl}")
                    write(repo / CANON / "t.md", canonical_header + canonical_body)
                    write(repo / "facts" / "t.md", legacy_header + legacy_body)
                    git(repo, "add", "-A")
                    git(repo, "commit", "-m", "fixtures")

                    result = layout.migrate_legacy_facts(repo)

                    if any("kept separate" in line for line in result.moved):
                        asides.append(f"{hc}/{hl} {bc}/{bl}")
    assert asides == [], f"ordinary collisions refused: {asides}"


def test_a_differing_topic_stays_searchable(tmp_path):
    """The value readers project into a row: it must survive the migration, not become prose."""
    repo = make_repo(tmp_path)
    write(repo / CANON / "deploys.md", "---\ntopic: deploy-runbook\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "deploys.md", "---\ntopic: incident-response\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    layout.migrate_legacy_facts(repo)

    topics = set()
    for path in (repo / CANON).rglob("*.md"):
        meta, _b = units.parse_frontmatter(path.read_text(encoding="utf-8"))
        if "topic" in meta:
            topics.add(meta["topic"])
    assert {"deploy-runbook", "incident-response"} <= topics


def test_the_merge_paths_note_stream_is_pinned_exactly(tmp_path):
    """The four notes a successful MERGE can emit, pinned by count as well as content.

    Not every note the module can emit — the metadata notes belong to `_carry_meta` and the
    demotion branch, and the refusal note replaces the whole stream when it fires; those
    have their own tests above. This one exists because relaxing five exact-list assertions
    to make room for a new note left the merge path unpinned past its first line.

    Relaxing the suite's exact-list assertions to make room for a new note left the stream
    unpinned past its first line — a spurious, duplicated or silently dropped note would be
    invisible. One fixture exercises all four notes at once and asserts the set.
    """
    repo = make_repo(tmp_path)
    shared = "- [gotcha] A sentence both files carry here for thirty seconds #x (verified: 2026-08-12)\n"
    write(repo / CANON / "t.md", "---\ntopic: t\n---\n" + shared)
    write(
        repo / "facts" / "t.md",
        # An identical header (nothing to carry, nothing differing), the same bullet
        # (duplicate fold), a bullet sharing its topic key (divergent), and prose (verbatim).
        "---\ntopic: t\n---\n"
        + shared
        + "- [gotcha] A sentence both files carry here for sixty seconds #y (verified: 2026-08-12)\n"
        + "some prose a human wrote\n",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    notes = " || ".join(result.merged)
    assert result.merged[0].startswith("facts/t.md merged into")
    assert "share a topic key" in notes
    assert "already said what a canonical bullet says" in notes  # the fold is reported
    assert "unparsed line(s) carried over verbatim" in notes
    assert len(result.merged) == 4, result.merged  # no note appears twice, none is missing


def test_the_refusal_note_names_what_it_protected(tmp_path):
    """The notes a refused merge would have emitted die with it, so the refusal carries them.

    A bare count names neither the topic nor the fact, leaving a reviewer to diff for what
    the migration declined to decide. Naming only the sentence and the category was worse
    than a count in the commonest case: both were still plainly in the canonical file.
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", "---\ntopic: deploy-runbook\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "t.md", "---\ntopic: incident-response\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    note = " ".join(result.moved)
    assert "incident-response" in note  # the topic that would have stopped labelling facts
    assert "Legacy bullet arriving in the merge" in note  # and the fact it labels
    assert "#y" in note and "2026-08-12" in note  # rendered as its author wrote it


def test_a_blank_topic_key_is_not_read_as_the_filename(tmp_path):
    """`meta.get("topic") or stem` is not the readers' lookup — `meta.get("topic", stem)` is.

    `or` maps a present-but-EMPTY `topic:` back to the stem; `.get(key, default)` keeps the
    empty string, and every reader uses the latter. A blank `topic:` parses and is
    lint-clean (MN009 checks presence), so it is what a hand-written header really carries,
    and reading it as the stem made the guard see one topic on both sides where the readers
    saw two — merging a blank topic over a named one and emptying the `name` column that
    `list_facts(topic=…)` filters on.
    """
    for label, canonical, legacy in [
        (
            "blank arriving from the legacy side",
            CANONICAL_BULLET,
            "---\ntopic:\nowner: sre\n---\n" + LEGACY_BULLET,
        ),
        (
            "blank already on the canonical side",
            "---\ntopic:\n---\n" + CANONICAL_BULLET,
            "---\ntopic: deploys\n---\n" + LEGACY_BULLET,
        ),
    ]:
        repo = make_repo(tmp_path / label.replace(" ", "-"))
        write(repo / CANON / "deploys.md", canonical)
        write(repo / "facts" / "deploys.md", legacy)
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "fixtures")
        before = topic_rows(tmp_path, repo, f"before-{label}")

        layout.migrate_legacy_facts(repo)

        assert topic_rows(tmp_path, repo, f"after-{label}") >= before, label


def test_the_topic_pin_agrees_with_the_parser_about_where_the_header_is(tmp_path):
    """The refusal path writes, so its write is measured like every other write here.

    `_pin_stem_topic` used this module's CR/LF-only splitting to decide whether the file
    had a frontmatter block, while `parse_frontmatter` uses `str.splitlines` — which also
    breaks on \\x0b, \\x0c, \\x85, \\x1c, \\u2028 and \\u2029. One of those in the opening
    delimiter line made the pin prepend a WHOLE NEW block above a header the parser was
    already reading, demoting every key in it to prose: the refusal path destroying the
    metadata it was invoked to protect.
    """
    for sep in ["\x0b", "\x0c", "\x85", "\x1c", " ", " "]:
        repo = make_repo(tmp_path / f"sep-{ord(sep)}")
        # A differing topic, so the merge is refused and the pin runs before the rename.
        write(repo / CANON / "t.md", "---\ntopic: other-topic\n---\n" + CANONICAL_BULLET)
        write(
            repo / "facts" / "t.md",
            f"---{sep}owner: sre\nsources: incident-4412\n---\n" + LEGACY_BULLET,
        )
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "fixtures")

        layout.migrate_legacy_facts(repo)

        meta, _b = units.parse_frontmatter(
            (repo / CANON / "t.legacy.md").read_text(encoding="utf-8")
        )
        assert meta.get("owner") == "sre", f"{sep!r} lost owner: {meta}"
        assert meta.get("sources") == "incident-4412", f"{sep!r} lost sources: {meta}"
        assert meta.get("topic") == "t", f"{sep!r} did not pin the topic: {meta}"


def test_the_demotion_note_explains_the_dropped_delimiters(tmp_path):
    """The one clause added in an earlier round that no test asserted."""
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", CANONICAL_BULLET)
    write(repo / "facts" / "t.md", "---\nnot a key line\nalso not one\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    notes = " ".join(result.merged)
    assert "`---` delimiters are not carried" in notes
    assert "read as a header on the next pass" in notes


def test_the_refusal_note_warns_that_names_and_ids_move(tmp_path):
    """The aside path renames the file, which moves both its unit ids and — when the file
    has no `topic:` of its own — the topic every reader falls back to the stem for."""
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", "---\nnot a key line\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "t.md", LEGACY_BULLET)  # no header, so the stem is the topic
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    note = " ".join(result.moved)
    assert "kept separate" in note
    assert "unit ids move" in note
    assert "topic" in note  # the stem-derived topic moves with the name too


def test_the_aside_walk_gives_up_rather_than_overwrite(tmp_path):
    """`.legacy`, `.legacy-2`, `.legacy-3`… and a clear error when every name is taken."""
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", "---\nnot a key line\n---\n" + CANONICAL_BULLET)
    for attempt in range(1, 100):
        suffix = ".legacy" if attempt == 1 else f".legacy-{attempt}"
        write(repo / CANON / f"t{suffix}.md", "- [gotcha] occupied #z (verified: 2026-08-12)\n")
    write(repo / "facts" / "t.md", LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    with pytest.raises(MnemeError) as exc:
        layout.migrate_legacy_facts(repo)

    assert "are all taken" in str(exc.value)
    assert (repo / "facts" / "t.md").is_file()  # nothing moved, nothing overwritten


def test_a_legacy_directory_that_cannot_be_emptied_is_reported(tmp_path):
    """`_drop_empty` refuses while anything remains rather than leaving a half-migrated repo."""
    repo = make_repo(tmp_path)
    write(repo / "facts" / "t.md", LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")
    # An entry the walk never sees: created after iterdir would have listed the directory.
    original = layout._migrate_into

    def leave_something(*args, **kwargs):
        original(*args, **kwargs)
        (repo / "facts" / "leftover.txt").write_text("x", encoding="utf-8")

    layout._migrate_into = leave_something
    try:
        with pytest.raises(MnemeError) as exc:
            layout.migrate_legacy_facts(repo)
    finally:
        layout._migrate_into = original

    assert "still holds" in str(exc.value)


def test_a_canonical_file_with_no_trailing_newline_is_terminated_before_appending(tmp_path):
    """Appending past a file that stopped mid-line must not glue two lines together."""
    repo = make_repo(tmp_path)
    canonical = write(repo / CANON / "t.md", "---\ntopic: t\n---\n" + CANONICAL_BULLET.rstrip("\n"))
    write(repo / "facts" / "t.md", "---\ntopic: t\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    layout.migrate_legacy_facts(repo)

    lines = canonical.read_text(encoding="utf-8").splitlines()
    assert "Canonical bullet that was already retrievable" in lines[-2]
    assert "Legacy bullet arriving in the merge" in lines[-1]


def test_the_report_says_when_a_header_was_demoted(tmp_path):
    """A migration that could not keep a header as metadata must say so, not report success.

    lint will flag nothing here — the merged file parses — but the reviewer of the pull
    request should know the legacy header became prose rather than keys.
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", CANONICAL_BULLET)
    write(repo / "facts" / "t.md", "---\ntopic: t\nnot a key line\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    notes = " ".join(result.merged)
    assert "header" in notes.lower()
    assert "body" in notes.lower()
