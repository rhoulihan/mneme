"""`distill ingest` is the untrusted door — the locks that were open.

Four defects, all reproduced against v0.9.0 before these tests existed, all filed in
`docs/superpowers/backlog/2026-09-11-ingest-hardening.md`. They surfaced while specifying
the Funes integration, which turns this door into one a general user knocks on; they are
live defects on the shipped path regardless of whether that work proceeds.

`proposals.py` already states the doctrine three of them violate: "proposals arrive as LLM
output, so every unbounded string is a memory/ledger-bloat vector. Sizes are generous enough
that honest content never trips them."
"""
import json

import pytest

from mneme_core import proposals, registry, staging
from mneme_core.cli import main
from mneme_core.errors import MnemeError
from mneme_core.registry import Plugin

FACT = {
    "type": "fact", "edit": "new", "target": "team-kb", "topic": "webhooks",
    "category": "gotcha", "text": "The webhook replays for seventy two hours",
    "tags": ["webhooks"], "confidence": 0.9, "rationale": "measured",
}
SKILL = {
    "type": "skill", "edit": "new", "target": "team-kb", "name": "rotate-certs",
    "description": "How to rotate the edge certificates without dropping connections.",
    "procedure": "1. Drain the node\n2. Rotate\n3. Rejoin",
    "failure_pattern": "Rotating before draining drops in-flight connections.",
    "confidence": 0.9, "rationale": "measured",
}


def doc(*entries):
    return json.dumps({"proposals": list(entries)})


def ingest(tmp_path, home, capsys, payload, *extra):
    p = tmp_path / "proposals.json"
    p.write_text(payload, encoding="utf-8")
    code = main(["--home", str(home), "distill", "ingest", str(p),
                 "--source", "session:abc", *extra])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# --- 1. the uncapped fields --------------------------------------------------


@pytest.mark.parametrize(
    "entry,expected",
    [
        (dict(FACT, topic="a" * 5_000), f"topic exceeds {proposals.MAX_TOPIC} chars"),
        (dict(SKILL, name="a" * 5_000), f"name exceeds {proposals.MAX_NAME} chars"),
        (dict(FACT, tags=["b" * 5_000]), f"tag exceeds {proposals.MAX_TAG} chars"),
    ],
)
def test_an_oversized_field_is_refused(entry, expected):
    """The expected text is exact, not the field name: "tag" is a substring of the
    PRE-EXISTING "tags exceeds 20 entries" message, so a `field in errors[0]` assertion
    passed against an implementation that had no per-tag cap at all and mislabelled one
    oversized tag as twenty of them."""
    valid, errors = proposals.parse_proposals(doc(entry))
    assert valid == []
    assert len(errors) == 1
    assert expected in errors[0], errors[0][:200]


@pytest.mark.parametrize(
    "entry,field",
    [
        (dict(FACT, topic="a" * 5_000), "topic"),
        (dict(SKILL, name="a" * 5_000), "name"),
        (dict(FACT, tags=["b" * 5_000]), "tag"),
    ],
)
def test_the_rejection_message_does_not_quote_the_whole_value(entry, field):
    """The error is itself a bloat vector: the kebab check interpolated the value, so a
    100k topic produced a 100k rejection line that was printed and, with --clear-flags,
    reasoned about. Capping first means the message names the limit, not the payload."""
    _valid, errors = proposals.parse_proposals(doc(entry))
    assert len(errors[0]) < 500, f"rejection message is {len(errors[0])} chars"


def test_honest_content_is_untouched():
    """'Sizes are generous enough that honest content never trips them.'"""
    entry = dict(
        FACT,
        topic="oracle-vector-index-rebuild-after-schema-change",
        tags=["oracle", "vector-index", "schema-migration", "ora-29855"],
    )
    valid, errors = proposals.parse_proposals(doc(entry, SKILL))
    assert errors == []
    assert len(valid) == 2


def test_the_staged_candidate_stays_small(tmp_path, capsys):
    """The harm, measured: one proposal wrote a 300KB candidate file."""
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    code, _out, _err = ingest(
        tmp_path, home, capsys, doc(dict(FACT, topic="a" * 5_000, tags=["b" * 5_000]))
    )
    assert code == 2
    assert staging.load_candidates(home, include_quarantined=True) == []


# --- 2. the exit code --------------------------------------------------------


def test_every_proposal_rejected_exits_non_zero(tmp_path, capsys):
    home = tmp_path / "home"
    code, out, _err = ingest(tmp_path, home, capsys, doc(dict(FACT, topic="NOT_KEBAB")))
    assert "staged 0" in out and "rejected 1" in out
    assert code == 2, "a run that threw everything away reported success"


def test_a_malformed_document_keeps_its_own_code(tmp_path, capsys):
    """Distinct from the above so a caller can tell 'I sent junk' from 'I sent nothing
    usable' — one is a transport bug, the other is content to fix."""
    home = tmp_path / "home"
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    code = main(["--home", str(home), "distill", "ingest", str(p)])
    capsys.readouterr()
    assert code == 1


def test_a_partial_run_still_succeeds(tmp_path, capsys):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    code, out, _err = ingest(
        tmp_path, home, capsys, doc(FACT, dict(FACT, topic="NOT_KEBAB"))
    )
    assert "staged 1" in out and "rejected 1" in out
    assert code == 0, "one good proposal is not a failed run"


def test_an_empty_document_succeeds(tmp_path, capsys):
    home = tmp_path / "home"
    code, _out, _err = ingest(tmp_path, home, capsys, doc())
    assert code == 0


def test_a_run_that_staged_nothing_new_succeeds(tmp_path, capsys):
    """Nothing rejected and nothing staged is an ordinary quiet run, not a failure."""
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    ingest(tmp_path, home, capsys, doc(FACT))
    code, out, _err = ingest(tmp_path, home, capsys, doc(FACT))
    assert "skipped-duplicate 1" in out and "rejected 0" in out
    assert code == 0


def test_a_quarantined_candidate_counts_as_written(tmp_path, capsys):
    """Quarantine is a verdict on a candidate that WAS staged; the run captured something,
    so it is not the empty-handed case the exit code is for."""
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    secret = dict(FACT, text="Use AKIAIOSFODNN7EXAMPLE with the staging endpoint")
    code, out, _err = ingest(
        tmp_path, home, capsys, doc(secret, dict(FACT, topic="NOT_KEBAB"))
    )
    assert "quarantined 1" in out and "rejected 1" in out
    assert code == 0


# --- 3. --source reaches a commit trailer ------------------------------------


@pytest.mark.parametrize(
    "bad", ["session:a\nMneme-Source: forged", "session:a\rx", "session:a\x00x"]
)
def test_a_control_character_in_source_is_refused(tmp_path, capsys, bad):
    """`gitops.commit_harvest` interpolates it into `Mneme-Source: {s}`, so a newline
    forges trailers. Checked where the value enters, not at the one site that formats it."""
    home = tmp_path / "home"
    p = tmp_path / "proposals.json"
    p.write_text(doc(FACT), encoding="utf-8")
    code = main(["--home", str(home), "distill", "ingest", str(p), "--source", bad])
    err = capsys.readouterr().err
    assert code == 1
    assert "source" in err.lower()


def test_an_overlong_source_is_refused(tmp_path, capsys):
    home = tmp_path / "home"
    p = tmp_path / "proposals.json"
    p.write_text(doc(FACT), encoding="utf-8")
    code = main(["--home", str(home), "distill", "ingest", str(p), "--source", "s" * 5_000])
    capsys.readouterr()
    assert code == 1


def test_an_ordinary_source_is_untouched(tmp_path, capsys):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    code, _out, _err = ingest(
        tmp_path, home, capsys, doc(FACT),
        *("--source", "session:/home/u/.claude/projects/x/abc-123.jsonl"),
    )
    assert code == 0


# --- 4. a machine-readable result --------------------------------------------


def test_json_output_names_what_was_staged(tmp_path, capsys):
    """Today ingest prints counters and English. A caller cannot learn a candidate's id
    without recomputing `candidate_id` itself — reimplementing mneme's rendering and
    hashing to read its own output."""
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    code, out, _err = ingest(tmp_path, home, capsys, doc(FACT), "--json")
    assert code == 0
    result = json.loads(out)
    assert result["schema_version"] == 1
    assert len(result["candidates"]) == 1
    entry = result["candidates"][0]
    cand = staging.load_candidates(home)[0]
    assert entry["id"] == cand.id
    assert entry["body"] == cand.body
    assert entry["target"] == "team-kb"
    assert entry["status"] == "staged"


def test_json_output_names_each_rejection_with_its_index(tmp_path, capsys):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    code, out, _err = ingest(
        tmp_path, home, capsys, doc(FACT, dict(FACT, topic="NOT_KEBAB")), "--json"
    )
    result = json.loads(out)
    assert code == 0
    assert [r["index"] for r in result["rejected"]] == [1]
    assert "kebab" in result["rejected"][0]["reason"]


def test_json_output_carries_scan_findings(tmp_path, capsys):
    """`cli.py` computes findings and discards all but `status`, so a caller cannot see
    WHY a candidate was quarantined."""
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    secret = dict(FACT, text="Use AKIAIOSFODNN7EXAMPLE with the staging endpoint")
    _code, out, _err = ingest(tmp_path, home, capsys, doc(secret), "--json")
    entry = json.loads(out)["candidates"][0]
    assert entry["status"] == "quarantined"
    assert entry["findings"], "no findings reported for a quarantined candidate"


def test_json_is_the_only_thing_on_stdout(tmp_path, capsys):
    """A caller pipes stdout to a parser; a stray human line breaks it."""
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    _code, out, _err = ingest(
        tmp_path, home, capsys, doc(FACT, dict(FACT, topic="NOT_KEBAB")), "--json"
    )
    json.loads(out)  # raises if anything else was printed


def test_the_human_output_is_unchanged_without_the_flag(tmp_path, capsys):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    _code, out, _err = ingest(tmp_path, home, capsys, doc(FACT))
    assert out.startswith("staged 1  quarantined 0")


# --- 5. the registry round-trip ----------------------------------------------


def test_an_unknown_plugin_key_survives_a_write_by_a_binary_that_lacks_it(tmp_path):
    """`load_registry` filters to known fields, so an older binary READS a newer registry
    safely — and then `save_registry` rewrites the file from the filtered objects and the
    field is gone, silently, for every plugin. It fails OPEN: a dropped sensitivity
    binding is not a refusal, it is a missing check."""
    from mneme_core import paths

    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))

    p = paths.registry_path(home)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["plugins"][0]["funes_scope"] = "acme-billing"
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")

    registry.add_plugin(home, Plugin(name="ops-kb", repo="r2", path=str(tmp_path / "ops")))

    after = json.loads(p.read_text(encoding="utf-8"))
    entry = next(e for e in after["plugins"] if e["name"] == "team-kb")
    assert entry.get("funes_scope") == "acme-billing"


def test_an_unknown_top_level_key_survives(tmp_path):
    from mneme_core import paths

    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    p = paths.registry_path(home)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["defaults"] = {"sensitivity": "restricted"}
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")

    registry.add_plugin(home, Plugin(name="ops-kb", repo="r2", path=str(tmp_path / "ops")))
    assert json.loads(p.read_text(encoding="utf-8"))["defaults"] == {
        "sensitivity": "restricted"
    }


def test_a_newer_registry_is_not_rewritten_by_an_older_binary(tmp_path):
    """Preserving unknown keys handles a field this binary has not learned. A higher
    version says the SHAPE changed, which preservation cannot promise to survive — so the
    write is refused rather than guessed at."""
    from mneme_core import paths

    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    p = paths.registry_path(home)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["version"] = 99
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")

    with pytest.raises(MnemeError) as e:
        registry.add_plugin(home, Plugin(name="ops-kb", repo="r2", path=str(tmp_path / "o")))
    # "version 99", not "99": the message embeds the registry PATH, and pytest's basetemp
    # is /tmp/pytest-of-<user>/pytest-<N> — so a bare "99" passes on run 99, 199, 299...
    assert "version 99" in str(e.value)
    after = json.loads(p.read_text(encoding="utf-8"))
    assert after["version"] == 99, "the refused write happened anyway"
    assert [e["name"] for e in after["plugins"]] == ["team-kb"]


def test_round_tripping_does_not_invent_keys(tmp_path):
    """The preservation must not leak an internal carrier field into the file."""
    from mneme_core import paths

    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    entry = json.loads(paths.registry_path(home).read_text(encoding="utf-8"))["plugins"][0]
    assert set(entry) == {"name", "repo", "path", "sensitivity", "exclusions"}


def test_a_retired_key_is_dropped_while_an_unknown_one_is_kept(tmp_path):
    """The two rules look alike and are opposite, so hold them in one file at once.

    `mode` was the pr|commit split, removed by user direction because mneme never writes a
    registered repo's main — a resave must garbage-collect it. A key this binary has simply
    never heard of is the other case entirely, and dropping it fails open.
    """
    from mneme_core import paths

    home = tmp_path / "home"
    paths.ensure_layout(home)
    paths.registry_path(home).write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": [
                    {
                        "name": "old-kb", "repo": "r", "path": str(tmp_path / "kb"),
                        "mode": "commit", "funes_scope": "acme-billing",
                        "sensitivity": "internal", "exclusions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry.save_registry(home, registry.load_registry(home))

    entry = json.loads(paths.registry_path(home).read_text(encoding="utf-8"))["plugins"][0]
    assert "mode" not in entry, "a retired key came back"
    assert entry["funes_scope"] == "acme-billing", "an unknown key was dropped"


def test_the_two_empty_handed_exit_codes_are_deliberate(tmp_path, capsys):
    """The same content problem exits 1 under --clear-flags and 2 without, and that is on
    purpose — so hold both in one test rather than letting a reader discover it.

    With --clear-flags the caller asked for a side effect that was then refused (the flags
    are kept for the next distiller), which `test_distill_ingest` groups with malformed
    JSON and a dead `claude`. Without it nothing was requested and nothing failed; the run
    simply captured nothing, which is what 2 means everywhere else in this CLI.
    """
    from mneme_core import flags as flags_mod

    bad = doc(dict(FACT, topic="NOT_KEBAB"))

    home_a = tmp_path / "a"
    flags_mod.add_flag(home_a, "hard-won fix worth keeping")
    code_a, _out, err = ingest(tmp_path, home_a, capsys, bad, "--clear-flags")
    assert code_a == 1
    assert "flags kept" in err
    assert len(flags_mod.read_flags(home_a)) == 1, "the flag was consumed anyway"

    home_b = tmp_path / "b"
    code_b, _out, _err = ingest(tmp_path, home_b, capsys, bad)
    assert code_b == 2


# --- 6. what `splitlines()` breaks on, which is more than ASCII ---------------

# Every character `str.splitlines()` honours. `_escape` handled two of the ten.
BREAKS = ["\n", "\v", "\f", "\r", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]


@pytest.mark.parametrize("sep", BREAKS)
def test_every_line_break_python_honours_is_refused_in_source(tmp_path, capsys, sep):
    """`units.parse_frontmatter` reads candidates back with `str.splitlines()`. Three of
    the ten breaks -- U+0085, U+2028, U+2029 -- are outside `[\\x00-\\x1f\\x7f]`, and
    `units._escape` did not escape them either, so they reached the file raw and split
    the record. One accepted `--source` value then broke every later `load_candidates`,
    including every subsequent ingest: the shipped pipeline dies silently."""
    home = tmp_path / "home"
    p = tmp_path / "proposals.json"
    p.write_text(doc(FACT), encoding="utf-8")
    code = main(["--home", str(home), "distill", "ingest", str(p),
                 "--source", "session:a" + sep + "x: y"])
    assert code == 1, f"{sep!r} was accepted into a frontmatter value"
    assert "source" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("sep", BREAKS)
def test_a_frontmatter_value_cannot_forge_another_field(tmp_path, sep):
    """The laundering defence. `source_sensitivity` is the one input
    `staging._boundary_for_move` says it cannot recover afterwards; if a value can inject
    a line, it sets that field -- and `target`, which harvest writes to -- to anything."""
    from mneme_core import staging as st

    home = tmp_path / "home"
    forged = ("a" + sep + "target: pub-kb" + sep + "source-sensitivity: public"
              + sep + "---" + sep + "z")
    cand = st.Candidate(
        id="fact-deadbeef", type="fact", edit="new", target="team-kb",
        body="- [gotcha] A thing that was learned #x (verified: 2026-09-11)\n",
        source_sensitivity="restricted", provenance={"source": forged},
    )
    st.write_candidate(home, cand)

    back = st.load_candidates(home, include_quarantined=True)
    assert len(back) == 1, "the record split in two"
    assert back[0].target == "team-kb", "target was forged through a frontmatter value"
    assert back[0].source_sensitivity == "restricted", "sensitivity was forged"
    assert back[0].provenance["source"] == forged, "the value did not round-trip"


def test_a_candidate_already_on_disk_cannot_forge_a_commit_trailer(tmp_path):
    """`harvest` reads `provenance.source` off the candidate FILE and hands it to
    `gitops.commit_harvest`, which formats `Mneme-Source: {s}`. Frontmatter escapes the
    newline and `units._unescape` restores it, so every candidate staged before the
    `--source` check still forges trailers after the upgrade. For harvest the boundary is
    the file, not the flag."""
    from mneme_core import gitops

    msg = gitops.harvest_message(
        ["facts/webhooks.md - a thing"],
        ["session:a\nMneme-Source: forged\nSigned-off-by: victim"],
        [],
    )
    # git parses trailers line by line, so the property is that no LINE reads as one --
    # the text may survive inline on the single legitimate trailer.
    assert len([ln for ln in msg.splitlines() if ln.startswith("Mneme-Source:")]) == 1
    assert not [ln for ln in msg.splitlines() if ln.startswith("Signed-off-by:")]


# --- 7. rejection messages, for every field that interpolates ----------------


@pytest.mark.parametrize(
    "entry",
    [
        dict(FACT, type="z" * 200_000),
        dict(FACT, edit="z" * 200_000),
        dict(FACT, category="z" * 200_000),
        dict(FACT, confidence=["Z" * 200_000]),
        dict(FACT, edit="update", target_unit="z" * 200_000),
    ],
)
def test_no_rejection_message_carries_an_unbounded_value(entry):
    """The rule this change states -- cap before anything interpolates -- was applied to
    three fields and missed four. `--json` carries these verbatim as `reason`, so an
    uncapped one makes the machine-readable result an unbounded carrier too."""
    _valid, errors = proposals.parse_proposals(doc(entry))
    assert len(errors) == 1
    assert len(errors[0]) < 500, f"rejection is {len(errors[0])} chars"


# --- 8. the registry must not read corruption as emptiness -------------------


@pytest.mark.parametrize("corrupt", ["{not json", "", "null", "[1, 2]"])
def test_a_corrupt_registry_raises_rather_than_reading_as_empty(tmp_path, corrupt):
    """Reading corruption as `[]` is the same fail-open this change exists to close: an
    empty registry means `routing.scopes()` is empty, so every boundary check is silently
    skipped -- and the next `registry add` rewrites the file with only the new plugin."""
    from mneme_core import paths

    home = tmp_path / "home"
    paths.ensure_layout(home)
    paths.registry_path(home).write_text(corrupt, encoding="utf-8")
    with pytest.raises(MnemeError):
        registry.load_registry(home)


def test_a_corrupt_registry_is_not_overwritten_by_the_next_add(tmp_path):
    from mneme_core import paths

    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "k")))
    registry.add_plugin(home, Plugin(name="ops-kb", repo="r2", path=str(tmp_path / "o")))
    p = paths.registry_path(home)
    original = p.read_text(encoding="utf-8")
    truncated = original[: len(original) // 2]
    p.write_text(truncated, encoding="utf-8")

    with pytest.raises(MnemeError):
        registry.add_plugin(home, Plugin(name="new", repo="r3", path=str(tmp_path / "n")))
    assert p.read_text(encoding="utf-8") == truncated, "the corrupt file was clobbered"


@pytest.mark.parametrize("version", [2.0, "2", "99", 99, True, None])
def test_a_version_that_is_not_a_plain_integer_is_refused(tmp_path, version):
    """JSON `2.0` parses to a float and `True` is an int subclass, so an
    `isinstance(int)` guard let both through and silently downgraded the file to 1."""
    from mneme_core import paths

    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "k")))
    p = paths.registry_path(home)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["version"] = version
    p.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(MnemeError):
        registry.load_registry(home)


def test_the_version_guard_holds_on_load_not_only_on_save(tmp_path):
    """Save-only, a v99 file LOADS with v1 semantics: if v2 renamed `sensitivity`,
    `load_registry` fills the v1 default and every boundary check runs on a value that is
    not in the file. The check belongs where the document is read."""
    from mneme_core import paths

    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "k")))
    p = paths.registry_path(home)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["version"] = 99
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(MnemeError) as e:
        registry.load_registry(home)
    assert "version 99" in str(e.value)


@pytest.mark.parametrize(
    "document",
    [
        {"version": 1, "plugins": ["not-an-object"]},
        {"version": 1, "plugins": [{"repo": "r", "path": "/p"}]},
        {"version": 1, "plugins": {"team-kb": {}}},
        {"version": 1, "plugins": "team-kb"},
    ],
)
def test_a_malformed_registry_entry_is_an_error_not_a_traceback(tmp_path, document):
    from mneme_core import paths

    home = tmp_path / "home"
    paths.ensure_layout(home)
    paths.registry_path(home).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(MnemeError):
        registry.load_registry(home)


# --- 9. the exit code and flag consumption must agree ------------------------


def test_flags_are_not_consumed_by_a_run_that_reports_it_captured_nothing(
    tmp_path, capsys
):
    """`_clear_ingested_flags` counted skipped-duplicate as handled and cleared; the exit
    code counted only staged+quarantined and returned 2. So the caller was told "I
    captured none of what you gave me" -- the one code it would retry on -- after the
    flags it would retry FROM had been destroyed. One definition, asked twice."""
    from mneme_core import flags as flags_mod

    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    ingest(tmp_path, home, capsys, doc(FACT))

    flags_mod.add_flag(home, "hard-won fix worth keeping")
    code, _out, _err = ingest(
        tmp_path, home, capsys, doc(FACT, dict(FACT, topic="NOT_KEBAB")), "--clear-flags"
    )
    # Both halves concretely. A biconditional alone is satisfied by being wrong twice:
    # reverting `handled` to staged+quarantined makes this run exit 1 AND keep the flags,
    # which still "agrees" — and a run that only ever dedups would then exit non-zero
    # forever and never drain a flag in the shipped pipeline.
    assert code == 0, "a run that deduplicated a known fact is not a failed run"
    assert flags_mod.read_flags(home) == [], "the flags were not consumed"


@pytest.mark.parametrize("sep", BREAKS)
def test_a_line_break_alone_forces_quoting(tmp_path, sep):
    """`_quote_if_needed` decides whether a value is escaped at all, and its triggers were
    a colon, a leading sigil, and `\\n\\r\\t\\\\"`. A value whose ONLY unusual character is
    one of the other seven breaks passed every trigger, went out unquoted, and split its
    record with no escaping applied. The forging test does not reach this path: its
    payload contains a colon, which quotes it anyway.
    """
    from mneme_core import staging as st

    home = tmp_path / "home"
    value = "alpha" + sep + "beta"
    st.write_candidate(home, st.Candidate(
        id="fact-cafebabe", type="fact", edit="new", target="team-kb",
        body="- [gotcha] A thing that was learned #x (verified: 2026-09-11)\n",
        provenance={"source": value},
    ))
    back = st.load_candidates(home, include_quarantined=True)
    assert len(back) == 1, f"{sep!r} split the record"
    assert back[0].provenance["source"] == value


# --- 10. the --json contract, not just the keys the tests happened to name ---

CANDIDATE_KEYS = {
    "id", "type", "edit", "target", "status", "body",
    "boundary_warning", "similar_to", "findings",
}


def test_the_json_contract_is_an_exact_shape(tmp_path, capsys):
    """Nine keys are emitted and the tests named four, so five could change or vanish
    silently. An exact set is the same idiom `test_round_tripping_does_not_invent_keys`
    uses on the registry."""
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    _code, out, _err = ingest(tmp_path, home, capsys, doc(FACT), "--json")
    result = json.loads(out)
    assert set(result) == {"schema_version", "counts", "candidates", "rejected"}
    assert set(result["candidates"][0]) == CANDIDATE_KEYS
    assert set(result["counts"]) == {
        "staged", "quarantined", "skipped_declined", "skipped_duplicate",
        "skipped_routed", "rejected", "boundary_warnings",
    }


def test_json_and_human_output_report_the_same_counts(tmp_path, capsys):
    """Two renderings of one run. Nothing held them together, so they could drift."""
    secret = dict(FACT, topic="creds", text="Use AKIAIOSFODNN7EXAMPLE at the endpoint")
    payload = doc(FACT, secret, dict(FACT, topic="NOT_KEBAB"))

    home_a = tmp_path / "a"
    registry.add_plugin(home_a, Plugin(name="team-kb", repo="r", path=str(tmp_path / "k")))
    _c, human, _e = ingest(tmp_path, home_a, capsys, payload)

    home_b = tmp_path / "b"
    registry.add_plugin(home_b, Plugin(name="team-kb", repo="r", path=str(tmp_path / "k")))
    _c, machine, _e = ingest(tmp_path, home_b, capsys, payload, "--json")

    counts = json.loads(machine)["counts"]
    first = human.splitlines()[0]
    assert f"staged {counts['staged']}" in first
    assert f"quarantined {counts['quarantined']}" in first
    assert f"rejected {counts['rejected']}" in first
    assert counts["staged"] == 1 and counts["quarantined"] == 1
    assert counts["rejected"] == 1


def test_a_quarantined_body_is_not_written_to_stdout(tmp_path, capsys):
    """The human path printed counters only; `--json` pipes candidate bodies. A
    quarantined body is precisely what the scan objected to, it cannot ship until the
    finding is resolved, and stdout is a place callers pipe into logs."""
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    secret = dict(FACT, text="Use AKIAIOSFODNN7EXAMPLE with the staging endpoint")
    _code, out, _err = ingest(tmp_path, home, capsys, doc(secret), "--json")

    assert "AKIAIOSFODNN7EXAMPLE" not in out
    entry = json.loads(out)["candidates"][0]
    assert entry["status"] == "quarantined"
    assert entry["body"] is None
    # The finding still says what was wrong, with the excerpt `scan._redact` truncated.
    assert entry["findings"][0]["excerpt"] == "AKIA…"


def test_json_survives_a_run_with_every_outcome_at_once(tmp_path, capsys):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    # Distinct TEXTS, not distinct topics: `candidate_id` hashes the rendered body, and a
    # fact body is `[category] text #tags` — the topic is not in it, so two proposals that
    # differ only by topic are one candidate.
    seen = dict(FACT, text="The retry budget resets at midnight UTC")
    ingest(tmp_path, home, capsys, doc(seen))
    payload = doc(
        FACT,
        seen,
        dict(FACT, text="Use AKIAIOSFODNN7EXAMPLE at the staging endpoint"),
        dict(FACT, topic="NOT_KEBAB", text="A fourth and different sentence entirely"),
    )
    code, out, _err = ingest(tmp_path, home, capsys, payload, "--json")
    result = json.loads(out)
    assert code == 0
    assert result["counts"]["skipped_duplicate"] == 1
    assert len(result["candidates"]) == 2
    assert len(result["rejected"]) == 1


def test_json_output_stays_parseable_with_clear_flags(tmp_path, capsys):
    from mneme_core import flags as flags_mod

    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    flags_mod.add_flag(home, "something worth keeping happened")
    _code, out, _err = ingest(tmp_path, home, capsys, doc(FACT), "--json", "--clear-flags")
    assert json.loads(out)["counts"]["staged"] == 1


# --- 11. the remaining edges ------------------------------------------------


def test_the_source_cap_holds_at_its_boundary(tmp_path, capsys):
    """Pinned both ways: a real transcript path must pass, and one character over must
    not. Asserting only that a 5,000-char source is refused pins nothing but `cap < 5000`,
    which a cap of 48 also satisfies — and 48 rejects an ordinary session path."""
    from mneme_core.cli import MAX_SOURCE

    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    p = tmp_path / "proposals.json"
    p.write_text(doc(FACT), encoding="utf-8")

    ok = main(["--home", str(home), "distill", "ingest", str(p),
               "--source", "s" * MAX_SOURCE])
    capsys.readouterr()
    assert ok == 0, f"a source of exactly MAX_SOURCE ({MAX_SOURCE}) was refused"

    over = main(["--home", str(home), "distill", "ingest", str(p),
                 "--source", "s" * (MAX_SOURCE + 1)])
    capsys.readouterr()
    assert over == 1


def test_a_retired_key_is_matched_exactly_not_by_prefix(tmp_path):
    """`mode` is retired. `modes`, or any future key that merely starts with it, is not —
    a prefix match would silently drop a field nobody decided to remove."""
    from mneme_core import paths

    home = tmp_path / "home"
    paths.ensure_layout(home)
    paths.registry_path(home).write_text(
        json.dumps({"version": 1, "plugins": [{
            "name": "team-kb", "repo": "r", "path": str(tmp_path / "kb"),
            "mode": "commit", "modes": ["a"], "sensitivity": "internal", "exclusions": [],
        }]}),
        encoding="utf-8",
    )
    registry.save_registry(home, registry.load_registry(home))
    entry = json.loads(paths.registry_path(home).read_text(encoding="utf-8"))["plugins"][0]
    assert "mode" not in entry
    assert entry["modes"] == ["a"]


def test_a_rejection_that_was_not_formatted_by_this_module_keeps_its_text():
    """`rejection_parts` owns the format; a string that does not match it must come back
    whole with no index, rather than being silently truncated or mis-attributed."""
    assert proposals.rejection_parts("something else entirely") == (
        None, "something else entirely"
    )
    assert proposals.rejection_parts(proposals.rejection(7, "a reason")) == (7, "a reason")


# --- 12. the caps must be generous, and only literals can prove it ----------


def test_realistic_content_passes_every_cap(tmp_path, capsys):
    """The one property `proposals.py`'s own doctrine claims — "generous enough that
    honest content never trips them" — and the one a boundary test cannot prove.

    `"x" * proposals.MAX_TOPIC` reads the constant, so it passes at ANY value: shrinking
    the caps to 48/41/17/48 leaves the whole suite green. Only literal content that a real
    session would produce pins a cap from below, so every value here is deliberately
    written out rather than generated, and must not be replaced with one derived from the
    constant it exists to hold up.
    """
    topic = "claude-code-plugin-manifest-and-hook-wiring-gotchas"      # 50
    name = "rotate-edge-certificates-without-dropping-connections"     # 52
    tags = ["oracle-vector-search", "converged-json-duality", "ora-29855"]
    # As `bin/mneme-distill-pipeline` builds it: "session:$(basename "$TRANSCRIPT")".
    source = "session:12a30bb0-0e2f-4456-92fb-214f013f408b.jsonl"      # 50

    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(tmp_path / "kb")))
    payload = doc(dict(FACT, topic=topic, tags=tags), dict(SKILL, name=name))
    p = tmp_path / "proposals.json"
    p.write_text(payload, encoding="utf-8")

    code = main(["--home", str(home), "distill", "ingest", str(p), "--source", source])
    out = capsys.readouterr().out
    assert code == 0, f"honest content was refused: {out}"
    assert "staged 2" in out
    assert "rejected 0" in out
