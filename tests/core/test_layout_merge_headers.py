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
  2. every legacy FRONTMATTER line survives somewhere in it,
  3. the index yields no fewer fact rows than before the merge.

Property 2 says frontmatter deliberately, and it is narrower than it once was. The merge
folds a legacy bullet away when a canonical bullet already says the same sentence: both
files share a stem, so both renderings share a unit id, and `index_tree` never held more
than one of them. That folded line leaves the working tree — it survives in the note, the
pull request diff and git history, not in the file — so "every legacy line survives" is no
longer true and stating it that way is how the next reader re-derives the wrong invariant.
`_lost` in the module under test draws exactly this line, and `assert_merge_preserved`
below matches it.
"""
import subprocess
from pathlib import Path

import pytest

from mneme_core import classify, layout, lint, units
from mneme_core.errors import MnemeError
from mneme_index import build, db

CANON = units.FACTS_CANONICAL

# Absolute, deliberately NOT `layout._NOTE_MAX`/`_BODY_MAX`. The harms these guard are
# absolute thresholds outside this codebase — a pull request body stops being accepted
# around 65 KB (past which `gitops.open_pr` silently returns its no-PR fallback and the
# review gate is gone), and `git commit -m` raises E2BIG past the platform argument limit.
# A test that asserts `<= layout._NOTE_MAX` reads the constant it is supposed to be
# guarding, so it cannot notice that constant being wrong: an earlier version of this file
# did exactly that, and the 94 KB note it was written to catch came back and passed,
# silently clamped by the very backstop the assertion was reading.
PR_BODY_LIMIT = 65_536
SAFE_NOTE = 16_000
SAFE_BODY = 60_000

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
    aside = repo / CANON / "t-legacy.md"
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
    assert not (repo / CANON / "t-legacy.md").exists()
    assert "owner: sre-oncall-team" in " ".join(result.merged)


def test_an_ordinary_pre_0_5_collision_merges_rather_than_piling_up_asides(tmp_path):
    """The migration's own function, measured: refusing loses nothing and achieves nothing.

    A guard that treated every metadata value and every bullet rendering as retrievable
    declined 64% of realistic legacy/canonical pairs, leaving `<stem>-legacy.md` beside
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


def test_a_topic_that_labels_facts_cannot_stop_labelling_them(tmp_path):
    """`_lost`'s third property, which nothing else in the suite reaches.

    It is not subsumed by the row property, and the case that separates them is the
    commonest overlap a pre-0.5 repo has: the same file copied and re-topiced. Sharing a
    SENTENCE means sharing a unit id (`facts/<stem>#<key>`), so `_dedup(before)` holds only
    the canonical row and the row property is structurally blind — the topic property is
    the only thing standing between a merge and the disappearance of a topic an agent can
    route by. Every other differing-topic test here uses DIFFERENT sentences, so the row
    property fires first and this one never runs: deleting it from `_lost` left the whole
    suite green.
    """
    repo = make_repo(tmp_path)
    sentence = "the load balancer keeps stale targets in rotation after a deploy"
    write(
        repo / CANON / "deploys.md",
        f"---\ntopic: deploys\n---\n- [gotcha] {sentence} #x (verified: 2026-08-12)\n",
    )
    write(
        repo / "facts" / "deploys.md",
        f"---\ntopic: incident-response\n---\n- [constraint] {sentence} #lb (verified: 2026-01-01)\n",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")
    before = topic_rows(tmp_path, repo, "before")
    assert {name for name, _ in before} == {"deploys"}  # the duplicate id hides the other

    result = layout.migrate_legacy_facts(repo)

    after = topic_rows(tmp_path, repo, "after")
    assert before <= after
    assert "incident-response" in {name for name, _ in after}
    assert any("kept separate" in line for line in result.moved)


def test_the_one_thing_a_folded_duplicate_costs_is_paid_into_the_note(tmp_path):
    """The accepted cost of the fold, pinned so it stays accepted rather than forgotten.

    `_dedup` is scoped to the INDEX on purpose: `index_tree` drops a duplicate unit id, so
    a folded rendering was never in the index. But `classify._fact_entries` and
    `cli._verify_cmd` walk `units.fact_files` with no dedup, so while the two files coexist
    both renderings DO reach the librarian bundle and the staleness report, and after the
    merge only the canonical one does. That is the cost, it is real, and it is accepted:
    Plan 12's collision rule is that the canonical file wins, and `units.fact_text_hash`
    already treats the sentence alone as a fact's identity.

    What makes it acceptable is that the note carries the folded line out in full, so this
    test pins BOTH halves — the bundle shrinks, and the rendering it lost is in the report.
    """
    repo = make_repo(tmp_path)
    # A long sentence on purpose: tags and the `(verified:)` stamp are the TAIL of a
    # bullet, so a per-value cap removes precisely the fields this note exists to preserve.
    # Pinning it with a short sentence let a 160-char cap pass while the property was false.
    sentence = (
        "the load balancer keeps stale targets in rotation for roughly ninety seconds after "
        "a deploy finishes draining them, which shows up as intermittent five-oh-two "
        "responses from the edge for the rest of that minute and then clears on its own "
        "once the health checks agree the old targets are gone"
    )
    # The rendered bullet must exceed the OLD 160-char cap by enough that the tail — the
    # tags and the `(verified:)` stamp, which is the whole point of the record — falls
    # outside it. The previous fixture rendered to 156 characters, still UNDER 160, so the
    # raise from 160 to 300 was unpinned and the regression it reverted was undetectable.
    assert len(f"- [constraint] {sentence} #lb (verified: 2026-01-01)") > 260
    write(repo / CANON / "deploys.md", f"---\ntopic: deploys\n---\n- [gotcha] {sentence} #deploy (verified: 2026-08-12)\n")
    write(repo / "facts" / "deploys.md", f"---\ntopic: deploys\n---\n- [constraint] {sentence} #lb (verified: 2026-01-01)\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")
    notes = []
    before = classify._fact_entries(repo, notes)
    assert len(before) == 2  # both renderings reach the librarian while both files exist
    indexed_before = topic_rows(tmp_path, repo, "before")

    result = layout.migrate_legacy_facts(repo)

    after = classify._fact_entries(repo, notes)
    assert len(after) == 1 and after[0]["category"] == "gotcha"  # canonical wins
    # The index is untouched, which is the line between accepted cost and knowledge loss.
    assert topic_rows(tmp_path, repo, "after") == indexed_before
    note = " ".join(result.merged)
    # Both ENDS of the folded line, for a bullet longer than the cap. The category leads
    # and the tags and stamp trail, and those ends are the whole record — the sentence
    # itself is still in the canonical file, only this rendering is gone. A plain cap kept
    # the head and dropped exactly the part worth keeping, so the line is elided in the
    # MIDDLE and the ellipsis says so.
    assert "[constraint]" in note and "#lb" in note and "2026-01-01" in note
    assert "…" in note
    assert sentence[:40] in note and sentence[-40:] in note


def test_a_wholesale_restamped_topic_file_names_every_line_it_folded(tmp_path):
    """The note is the record, so it may not quietly stop at three.

    A topic file copied and re-verified wholesale is the realistic pre-0.5 shape this fold
    is built for: every bullet is a duplicate, so every rendering is folded, and a cap of
    three left the rest visible only in git history — truncating hardest exactly where the
    note is load-bearing.
    """
    repo = make_repo(tmp_path)
    sentences = [f"the {n} service keeps stale targets around" for n in "abcdefgh"]
    write(
        repo / CANON / "deploys.md",
        "---\ntopic: deploys\n---\n"
        + "".join(f"- [gotcha] {s} #deploy (verified: 2026-08-12)\n" for s in sentences),
    )
    write(
        repo / "facts" / "deploys.md",
        "---\ntopic: deploys\n---\n"
        + "".join(f"- [constraint] {s} #lb (verified: 2026-01-01)\n" for s in sentences),
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    note = " ".join(result.merged)
    assert "8 bullet(s)" in note
    for s in sentences:
        assert s in note, f"folded rendering not named in the note: {s}"


def test_past_the_cap_the_note_says_where_the_rest_are(tmp_path):
    """The truncation branch — the half of the note that only fires on a big fold.

    A note that stops at the cap without saying so reads as a complete list. The rest are
    recoverable only from the commit before the migration, so the note has to name it.
    """
    repo = make_repo(tmp_path)
    sentences = [f"the {n:02d} service keeps stale targets around" for n in range(60)]
    write(
        repo / CANON / "deploys.md",
        "---\ntopic: deploys\n---\n"
        + "".join(f"- [gotcha] {s} #deploy (verified: 2026-08-12)\n" for s in sentences),
    )
    write(
        repo / "facts" / "deploys.md",
        "---\ntopic: deploys\n---\n"
        + "".join(f"- [constraint] {s} #lb (verified: 2026-01-01)\n" for s in sentences),
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    note = " ".join(result.merged)
    assert "60 bullet(s)" in note
    assert "as of the commit before this migration" in note
    # The COUNT cap is the one meant to bind for ordinary bullets, and it must actually
    # bind: sizing the length budget independently (1200 characters) made it bind first for
    # any bullet over ~46 characters, quietly cutting the record to 10-14 lines while three
    # comments and this test still said 25. An assertion of merely "0 < shown < 60" is what
    # let that through, so this pins the number.
    shown = sum(1 for s in sentences if s in note)
    assert shown == layout._DUPLICATES_SHOWN == 25
    assert f"and {len(sentences) - shown} more" in note
    assert len(note) < SAFE_NOTE


def test_no_note_can_be_made_into_more_than_one_line(tmp_path):
    """Notes go into a commit body and a pull request body — `facts/` content is untrusted.

    A caller writes each note as `- {line}`, so a note that becomes nine physical lines
    puts eight of them at the left margin of the artifact a human reads to decide whether
    the migration was safe: forged `Mneme-*:` trailers, invented findings, arbitrary
    markdown. `topic:` values and filenames are both repo content, and `units._unescape`
    turns a `\\n` escape in a quoted value into a real newline.
    """
    forged = (
        'deploys\\n\\nMneme-Review: approved-by-security\\n\\n'
        '- [gotcha] migration completed with no findings #d (verified: 2026-08-12)'
    )
    for label, canonical, legacy in [
        ("a forged topic on the legacy side", "---\ntopic: t\n---\n" + CANONICAL_BULLET,
         f'---\ntopic: "{forged}"\n---\n' + LEGACY_BULLET),
        ("a forged topic on the canonical side", f'---\ntopic: "{forged}"\n---\n' + CANONICAL_BULLET,
         "---\ntopic: t\n---\n" + LEGACY_BULLET),
        ("a forged value in a carried key", "---\ntopic: t\nowner: platform\n---\n" + CANONICAL_BULLET,
         f'---\ntopic: t\nowner: "{forged}"\n---\n' + LEGACY_BULLET),
    ]:
        repo = make_repo(tmp_path / label.replace(" ", "-"))
        write(repo / CANON / "t.md", canonical)
        write(repo / "facts" / "t.md", legacy)
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "fixtures")

        result = layout.migrate_legacy_facts(repo)

        assert result.lines, label
        # The body exactly as a caller builds it (Plan 12 Task 3: `f"- {line}"` per note,
        # joined for the commit and the PR). The property is that repo content cannot
        # START a line: a git trailer, a markdown heading and a checklist item are all
        # line-anchored, so a value pinned inside one line is inert however it reads.
        body = "\n".join(f"- {line}" for line in result.lines)
        for physical in body.splitlines():
            assert physical.startswith("- "), f"{label}: content escaped its bullet: {physical!r}"
        assert len(body.splitlines()) == len(result.lines), label


def test_a_note_cannot_be_made_enormous(tmp_path):
    """Bounded values are not a bounded note — the multiplicities are repo-controlled too.

    An earlier version of this test used ONE vector, a 100 KB `topic:` scalar, which
    `_safe` already capped — so it passed while the property in its own name was false. A
    pair disagreeing on 1200 frontmatter keys assembled a 94 KB note out of 1200
    individually-tiny capped values, and one bullet carrying 12,000 tags produced 85 KB.
    Past ~65 KB `gitops.open_pr` silently returns its no-PR fallback (losing the review
    gate); past ~128 KB `git commit -m` raises E2BIG, an OSError that reaches
    `harvest._abort` and resets the pass's own work away.

    The realistic vector matters as much as the adversarial ones: 36 wholesale-restamped
    bullets with ordinary 95-character sentences — the exact shape the fold is built for —
    also broke the old bound.
    """
    def keyed(n, value):
        return "---\ntopic: t\n" + "".join(f"k{i}: {value}-{i}\n" for i in range(n)) + "---\n"

    long_sentences = [
        f"the {i:02d} service keeps stale targets around for a while after a deploy drains"
        for i in range(36)
    ]
    vectors = {
        "a 100 KB folded topic scalar, canonical side": (
            f"---\ntopic: {'x' * 100_000}\n---\n" + CANONICAL_BULLET,
            "---\ntopic: t\n---\n" + LEGACY_BULLET,
        ),
        # The side matters, and an earlier version of this test had it backwards. A refusal
        # names what it PROTECTED, which is the legacy file's topic and the legacy row —
        # so a huge value on the canonical side never reaches a note at all, and testing
        # only that side left the per-value cap unpinned.
        "a 100 KB folded topic scalar, legacy side": (
            "---\ntopic: t\n---\n" + CANONICAL_BULLET,
            f"---\ntopic: {'x' * 100_000}\n---\n" + LEGACY_BULLET,
        ),
        "a 100 KB sentence in a lost row": (
            "---\ntopic: alpha\n---\n" + CANONICAL_BULLET,
            "---\ntopic: beta\n---\n- [constraint] " + "long " * 20_000
            + "#y (verified: 2026-08-12)\n",
        ),
        "1200 differing frontmatter keys": (
            keyed(1200, "canonical-value") + CANONICAL_BULLET,
            keyed(1200, "legacy-value") + LEGACY_BULLET,
        ),
        "4000 differing frontmatter keys": (
            keyed(4000, "canonical-value") + CANONICAL_BULLET,
            keyed(4000, "legacy-value") + LEGACY_BULLET,
        ),
        "1200 carried frontmatter keys": (
            "---\ntopic: t\n---\n" + CANONICAL_BULLET,
            keyed(1200, "legacy-value") + LEGACY_BULLET,
        ),
        "12000 tags on a lost row": (
            "---\ntopic: alpha\n---\n" + CANONICAL_BULLET,
            "---\ntopic: beta\n---\n- [constraint] legacy bullet arriving in the merge "
            + " ".join(f"#t{i}" for i in range(12_000))
            + " (verified: 2026-08-12)\n",
        ),
        "36 restamped bullets, ordinary sentences": (
            "---\ntopic: t\n---\n"
            + "".join(f"- [gotcha] {s} #deploy (verified: 2026-08-12)\n" for s in long_sentences),
            "---\ntopic: t\n---\n"
            + "".join(f"- [constraint] {s} #lb (verified: 2026-01-01)\n" for s in long_sentences),
        ),
    }
    for label, (canonical, legacy) in vectors.items():
        repo = make_repo(tmp_path / str(abs(hash(label))))
        write(repo / CANON / "t.md", canonical)
        write(repo / "facts" / "t.md", legacy)
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "fixtures")

        result = layout.migrate_legacy_facts(repo)

        assert result.lines, label
        for line in result.lines:
            assert len(line) < SAFE_NOTE, f"{label}: note is {len(line)} chars"
            # Short is not the same as informative. `_note` clamps a whole note as a last
            # resort, which satisfies a length assertion while one repo value has crowded
            # out everything the note was for — and a clamped note is exactly the one that
            # ends mid-word in an ellipsis. Every note here should be brought under the
            # limit by its own list budgets, so the backstop must never be what did it.
            # This is what pins the per-value cap independently of that backstop.
            assert not line.endswith("…"), f"{label}: note was clamped whole: {line[-70:]!r}"
    # And the constants themselves stay inside the limits they exist to respect.
    assert layout._NOTE_MAX <= SAFE_NOTE < PR_BODY_LIMIT
    assert layout._BODY_MAX <= SAFE_BODY < PR_BODY_LIMIT


def test_the_body_is_bounded_even_when_every_single_note_is(tmp_path):
    """Bounded notes are not a bounded body — one note per file, times many files.

    A pre-0.5 repo with several hundred legacy topics reaches the same two cliffs a single
    huge note did: `open_pr` falling back to no PR at all, and `git commit -m` raising
    E2BIG into `harvest._abort`, which resets away the pass being recorded.
    """
    repo = make_repo(tmp_path)
    for i in range(600):
        write(repo / "facts" / f"topic-{i:03d}.md", f"---\ntopic: topic-{i:03d}\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    assert len(result.lines) == 600
    body = result.body()
    assert sum(len(line) + 1 for line in body) < SAFE_BODY
    # A small repo is never truncated, and a truncated one always says by how much.
    # The TRUNCATING path is the one that can exceed its own budget, because the line
    # that says "omitted" is itself part of the body. Measured at a budget small enough
    # to force it, not only at the default where nothing truncates.
    for budget in (200, 1000, 5000, 20_000):
        truncated = result.body(budget=budget)
        assert sum(len(line) + 1 for line in truncated) <= budget, budget
        assert "more migration note(s), omitted" in truncated[-1], budget
    assert len(result.body(budget=200)) < 600
    assert layout.MigrationResult(moved=["a", "b"]).body() == ["a", "b"]


def test_a_colliding_file_that_is_not_utf8_is_set_aside_not_a_hard_error(tmp_path):
    """Every other reader tolerates undecodable bytes; this module used to wedge on them.

    Once the migration runs on every branch flow, one bad byte in `facts/` would fail every
    classify, review and share finalize. It cannot be merged — there is no text to fold —
    so it takes the same aside path as every other unmergeable file, and its bytes are
    untouched.
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", "---\ntopic: t\n---\n" + CANONICAL_BULLET)
    raw = b"---\ntopic: t\n---\n- [gotcha] caf\xe9 latin-1 bytes here #y (verified: 2026-08-12)\n"
    (repo / "facts").mkdir(parents=True, exist_ok=True)
    (repo / "facts" / "t.md").write_bytes(raw)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    aside = repo / CANON / "t-legacy.md"
    assert aside.exists()
    assert aside.read_bytes() == raw  # not rewritten, not re-encoded, not pinned
    assert not (repo / "facts").exists()
    assert any("not valid UTF-8" in line for line in result.moved)
    assert "Canonical bullet" in (repo / CANON / "t.md").read_text(encoding="utf-8")


def test_the_migration_never_newly_blinds_a_file_to_lint(tmp_path):
    """MN009 and MN010 are the codes that mean a reader has lost the file.

    Lint is the fourth reader of a fact file's `topic` (`lint.lint_fact_file`), and MN009
    fires when that key is absent — so if `_carry_meta` could ever demote `topic` out of a
    header, lint would say so. MN010 means the file does not parse at all: invisible to the
    index, search, the classify bundle and lint's own bullet checks.

    MN006 is deliberately not in this property. An unterminated legacy header is MN010
    BEFORE the merge — the whole file unreadable — and after it those lines live in a file
    that parses, where lint can finally name the one malformed bullet. Trading "this file
    is invisible" for "this line is malformed" is the migration doing its job.
    """
    blinding = {"MN009", "MN010"}

    def blinded(repo):
        """Per FILE, not a repo-wide set or even a repo-wide count.

        A set comparison cannot see a newly blinded file whenever any OTHER file already
        carries that code — and the decoys below guarantee one always does, which is the
        realistic state of a pre-0.5 repo mid-migration. A repo-wide COUNT is barely
        better: the merge always deletes the legacy file, so every case comes with a
        guaranteed decrement that can mask a simultaneous increment on the surviving
        canonical file. Keying by file name fixes that for the ASIDE, whose name is absent
        from `before` entirely.

        It does NOT separate the two `t.md` fixtures from each other — they share a
        basename, so `Path(issue.path).name` collapses them — and claiming otherwise was
        the same overclaim this series keeps correcting. The table still kills the defect
        it was written for; it is simply not the airtight per-file measurement the earlier
        wording promised.
        """
        found = {}
        for issue in lint.lint_repo(repo):
            if issue.code in blinding:
                found.setdefault(Path(issue.path).name, set()).add(issue.code)
        return found

    headers = [
        "",
        "---\ntopic: t\n---\n",
        "---\nowner: platform\n---\n",  # no topic: MN009 before AND after
        "---\ntopic: t\n",  # unterminated: MN010 before
        "---\nnot a key line\ntopic: t\n---\n",
        "---\ntags:\n- a\n- b\n---\n",
    ]
    bodies = [CANONICAL_BULLET, LEGACY_BULLET, "- [broken not a bullet\nprose\n", ""]
    for i, canonical_header in enumerate(headers):
        for j, legacy_header in enumerate(headers):
            for k, canonical_body in enumerate(bodies):
                for m, legacy_body in enumerate(bodies):
                    repo = make_repo(tmp_path / f"{i}-{j}-{k}-{m}")
                    write(repo / CANON / "t.md", canonical_header + canonical_body)
                    write(repo / "facts" / "t.md", legacy_header + legacy_body)
                    # Decoys that ALREADY carry both codes, so a set comparison would be
                    # blind here and the count has to do the work.
                    write(repo / CANON / "decoy-no-topic.md", CANONICAL_BULLET)
                    write(repo / CANON / "decoy-unterminated.md", "---\ntopic: d\n" + CANONICAL_BULLET)
                    git(repo, "add", "-A")
                    git(repo, "commit", "-m", "fixtures")
                    before = blinded(repo)
                    codes_before = {c for codes in before.values() for c in codes}
                    assert codes_before == blinding  # the masking really is set up

                    layout.migrate_legacy_facts(repo)

                    after = blinded(repo)
                    for name, codes in after.items():
                        assert not codes - before.get(name, set()), (
                            f"{canonical_header!r} + {canonical_body!r} <- "
                            f"{legacy_header!r} + {legacy_body!r}: {name} gained "
                            f"{sorted(codes - before.get(name, set()))}"
                        )


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

    The table is every character the two splitters disagree about, not a sample of
    them: the argument this fix rests on is "write in the parser's own line space",
    so the test has to cover the whole of that line space.
    """
    for sep in ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]:
        assert len(f"a{sep}b".splitlines()) == 2, f"{sep!r} is not a splitlines break"
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
            (repo / CANON / "t-legacy.md").read_text(encoding="utf-8")
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
    # The clause about the pin, precisely. `assert "topic" in note` was satisfied by
    # `_describe_lost`'s own "… under topic “t”", so deleting this clause entirely left
    # the suite green — the assertion could not fail for the property in its docstring.
    assert "`topic: t` was written into it first" in note
    assert "survives the rename" in note
    # And the file really did get the key, not just the promise of it.
    meta, _b = units.parse_frontmatter(
        (repo / CANON / "t-legacy.md").read_text(encoding="utf-8")
    )
    assert meta["topic"] == "t"


def test_the_aside_walk_gives_up_rather_than_overwrite(tmp_path):
    """`-legacy`, `-legacy-2`, `-legacy-3`… and a clear error when every name is taken.

    A hyphen, not a dot: `t.legacy` is not kebab-case, so every unit id the aside minted
    would be unreachable by `mneme share apply` (`harvest._unit_path` proves the stem).
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", "---\nnot a key line\n---\n" + CANONICAL_BULLET)
    for attempt in range(1, 100):
        suffix = "-legacy" if attempt == 1 else f"-legacy-{attempt}"
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


def test_a_frontmatter_line_that_normalizes_equal_is_still_not_discarded(tmp_path):
    """`_lost`'s FOURTH property, which nothing else in the suite reaches.

    It is the property the whole loosening rests on: demoting a differing value into the
    body is only harmless because the LINE is still somewhere in the result. Replacing the
    check with `pass` left all 646 tests green while human-committed lines vanished.

    The case that reaches it is a normalization collision. `_meta_blocks` joins a key's
    block into one string, so `tags:\\n  - a\\n  - b` and `tags:\\n  - a  - b` become the
    identical value `tags: - a - b`; `_carry_meta` then takes its equal-value branch and
    drops the legacy block WITHOUT demoting it, because it believes the canonical file
    already says the same thing. Only property 4 notices that two lines a human wrote are
    now in no file at all.
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", "---\ntopic: t\ntags:\n  - a\n  - b\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / "t.md", "---\ntopic: t\ntags:\n  - a  - b\n---\n" + LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")

    result = layout.migrate_legacy_facts(repo)

    assert any("kept separate" in line for line in result.moved)
    assert "frontmatter: - a - b" in " ".join(result.moved)  # named, not just counted
    aside = (repo / CANON / "t-legacy.md").read_text(encoding="utf-8")
    assert "- a  - b" in aside  # the line a human wrote is still in the tree


def test_the_topic_pin_is_reverted_when_it_would_break_the_file(tmp_path):
    """The refusal path measures its OWN write — replacing that check with `if False:`
    left all 646 tests green, and the guard is the only thing standing between this
    module and parking an unreadable file in the canonical directory.

    A filename is repo content (`facts/` is untrusted by this module's own threat model)
    and a newline is legal in one. The stem then IS the topic value the pin writes, so
    `topic: a\\nb` puts a bare `b` on its own line inside the header, `parse_frontmatter`
    rejects the whole file, and the aside lands in the canonical directory invisible to
    the index, search, the classify bundle and lint's bullet checks.
    """
    repo = make_repo(tmp_path)
    name = "a\nb.md"
    # A differing topic forces the refusal; no legacy header means the pin runs.
    write(repo / CANON / name, "---\ntopic: other-topic\n---\n" + CANONICAL_BULLET)
    write(repo / "facts" / name, LEGACY_BULLET)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")
    before = fact_rows(tmp_path, repo, "before")

    result = layout.migrate_legacy_facts(repo)

    aside = repo / CANON / "a\nb-legacy.md"
    assert aside.exists()
    units.parse_frontmatter(aside.read_text(encoding="utf-8-sig"))  # must still parse
    assert fact_rows(tmp_path, repo, "after") >= before
    # And the `stranded` clause, which fires exactly here and nowhere else in the suite:
    # reverting the pin means the stem-derived topic really does move, so the note has to
    # say so rather than relabel a reader's facts silently. Setting `stranded = set()` or
    # deleting the clause left the whole suite green.
    note = " ".join(result.moved)
    assert "could not be written into its header" in note
    assert "set `topic:` by hand" in note
    assert "was written into it first" not in note  # nothing claims the pin succeeded


def test_the_readable_projection_uses_the_readers_own_line_splitting(tmp_path):
    """`_Readable`'s whole basis: it must be the readers' rows, not a re-derivation.

    `str.splitlines` breaks on \\x0b, \\x0c, \\x85, \\x1c, \\x1d, \\x1e and the two
    separator characters; this module's own `_line_contents` deliberately does not, because
    inside a bullet those bytes are data it must move verbatim. That difference is correct
    for deciding what to CARRY and wrong for deciding what a reader can SEE, and swapping
    one for the other in `_readable` left the whole suite green. Asserted against
    `build._fact_rows` directly, so the projection is compared with the reader rather than
    with another copy of the same assumption.
    """
    repo = make_repo(tmp_path)
    for i, sep in enumerate(["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]):
        body = (
            f"- [gotcha] alpha {i} one two three four #a (verified: 2026-08-12){sep}"
            f"- [constraint] beta {i} five six seven eight #b (verified: 2026-08-12)\n"
        )
        # NO frontmatter, deliberately. `parse_frontmatter` rejoins a header'd file's body
        # with "\n", which normalizes these separators away before `_readable` ever sees
        # them — so a fixture WITH a header cannot tell the two splittings apart, and the
        # first version of this test used one and passed under the very mutation it was
        # written to kill. A file with no header gets its raw text back as the body, which
        # is the only place the two diverge.
        path = write(repo / CANON / f"t{i}.md", body)

        rows = layout._readable(path, path.read_text(encoding="utf-8"))

        reader = [r for r in build._fact_rows("p", repo, []) if r[8].endswith(f"t{i}.md")]
        assert len(reader) == 2, f"{sep!r}: fixture did not produce two reader rows"
        assert {r[1] for r in reader} == set(rows.rows), f"{sep!r}: projection != reader"


def test_an_unterminated_block_is_not_a_block(tmp_path):
    """`_frontmatter_end`'s stated rule, which no test could fail for.

    Returning `len(lines)` for an unterminated block — treating everything below the
    opening delimiter as metadata — left the suite green, and the function's own docstring
    calls guessing in the lossy direction "the one thing it may not do": this module
    DELETES the file it reads, so a bullet mistaken for a frontmatter line is a bullet
    carried into a header instead of a body, where no reader will ever see it again.
    """
    repo = make_repo(tmp_path)
    write(repo / CANON / "t.md", "---\ntopic: t\n---\n" + CANONICAL_BULLET)
    legacy = "---\nowner: sre\n" + LEGACY_BULLET  # opens a block, never closes it
    write(repo / "facts" / "t.md", legacy)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixtures")
    before = fact_rows(tmp_path, repo, "before")

    layout.migrate_legacy_facts(repo)

    merged = (repo / CANON / "t.md").read_text(encoding="utf-8")
    meta, body = units.parse_frontmatter(merged)
    # The legacy lines travelled with the BODY, so the bullet is still a bullet...
    assert "Legacy bullet arriving in the merge" in body
    assert fact_rows(tmp_path, repo, "after") >= before
    # ...and none of them were promoted into the canonical header on a guess.
    assert "owner" not in meta


def test_the_note_clamp_is_a_backstop_that_never_has_to_fire(tmp_path):
    """`_note`'s clamp exists for the note a future edit forgets to budget.

    Removing it left the suite green, because every note the fixtures produce is already
    inside its own list budgets — which is the point, and also why the clamp needs its own
    test rather than an end-to-end one. Called directly: it is the difference between a
    bounded body and an E2BIG.
    """
    assert layout._note("x" * 500_000).endswith("…")
    assert len(layout._note("x" * 500_000)) == layout._NOTE_MAX
    assert layout._note("short note") == "short note"
    assert layout._note("a\nb\nc") == "a b c"  # one line, always


def test_join_capped_never_exceeds_its_budget(tmp_path):
    """The boundary itself: `>` versus `>=` left the suite green.

    Every note budget in this module is enforced here, so an off-by-one is the difference
    between a bound and a suggestion.
    """
    for budget in range(0, 60):
        joined, omitted = layout._join_capped(["abcde"] * 20, "; ", budget)
        assert len(joined) <= budget, (budget, joined)
        assert omitted == 20 - (len(joined.split("; ")) if joined else 0)
        # The budget is a bound, not a target to undershoot: whatever was left out has to
        # be something that genuinely did not fit. Without this, tightening `>` to `>=`
        # passes — every note simply carries one item less than it could, forever.
        if omitted:
            assert len(joined) + len("; ") + len("abcde") > budget, (budget, joined)
    joined, omitted = layout._join_capped([], "; ", 100)
    assert (joined, omitted) == ("", 0)
