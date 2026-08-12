import pytest

from mneme_core import compose, paths, staging
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id


def fact_body(text="DB resets nightly", verified="2026-08-11"):
    return compose.render_fact_bullet("constraint", text, ["staging"], verified=verified)


def skill_body(captured="2026-08-11", source="repo@session-1"):
    return compose.render_skill_unit(
        "deploy-widget", "Use when deploying widgets", "Steps.", "What failed first.",
        source=source, captured=captured,
    )


def make(target="acme-knowledge", body="# Skill body\n", **kw):
    cid = candidate_id("skill", target, body)
    defaults = dict(
        id=cid,
        type="skill",
        edit="new",
        target=target,
        body=body,
        confidence=0.8,
        rationale="hard-won deploy fix",
        provenance={"source": "repo@session-1", "captured": "2026-08-11"},
    )
    defaults.update(kw)
    return Candidate(**defaults)


def test_candidate_id_is_stable_and_type_prefixed():
    a = candidate_id("skill", "t", "body")
    assert a == candidate_id("skill", "t", "body")
    assert a.startswith("skill-")
    assert a != candidate_id("fact", "t", "body")


def test_write_and_load_round_trip(tmp_path):
    cand = make()
    path = staging.write_candidate(tmp_path, cand)
    assert path.parent == paths.staging_dir(tmp_path)
    loaded = staging.load_candidates(tmp_path)
    assert len(loaded) == 1
    got = loaded[0]
    assert got == cand


def test_update_requires_target_unit(tmp_path):
    cand = make(edit="update")
    with pytest.raises(MnemeError):
        staging.write_candidate(tmp_path, cand)
    ok = make(edit="update", target_unit="skills/deploy-widget")
    staging.write_candidate(tmp_path, ok)


def test_validation_rejects_bad_enum_values(tmp_path):
    with pytest.raises(MnemeError):
        staging.write_candidate(tmp_path, make(type="note"))
    with pytest.raises(MnemeError):
        staging.write_candidate(tmp_path, make(edit="patch"))
    with pytest.raises(MnemeError):
        staging.write_candidate(tmp_path, make(body="  "))


def test_quarantine_moves_and_marks(tmp_path):
    cand = make()
    staging.write_candidate(tmp_path, cand)
    qpath = staging.quarantine(tmp_path, cand.id)
    assert qpath.parent == paths.quarantine_dir(tmp_path)
    assert staging.load_candidates(tmp_path) == []
    quarantined = staging.load_candidates(tmp_path, include_quarantined=True)
    assert len(quarantined) == 1
    assert quarantined[0].status == "quarantined"


def test_remove_candidate(tmp_path):
    cand = make()
    staging.write_candidate(tmp_path, cand)
    staging.remove_candidate(tmp_path, cand.id)
    assert staging.load_candidates(tmp_path) == []
    with pytest.raises(MnemeError):
        staging.remove_candidate(tmp_path, cand.id)


def test_decline_records_and_removes(tmp_path):
    cand = make()
    staging.write_candidate(tmp_path, cand)
    assert not staging.is_declined(tmp_path, cand.body)
    staging.decline(tmp_path, cand, "not institutional knowledge")
    assert staging.is_declined(tmp_path, cand.body)
    assert staging.load_candidates(tmp_path) == []
    # same body re-proposed under a different target still matches the ledger
    assert staging.is_declined(tmp_path, "# Skill body\n")


def test_declined_fact_stays_declined_the_next_day(tmp_path):
    # The distiller re-renders the same knowledge with today's stamp on every run, so a
    # decline recorded yesterday must still match the body composed today.
    day1, day2 = fact_body(verified="2026-08-10"), fact_body(verified="2026-08-11")
    assert day1 != day2
    staging.decline(tmp_path, make(type="fact", body=day1), "not useful")
    assert staging.is_declined(tmp_path, day2)


def test_declined_skill_stays_declined_across_days_and_sessions(tmp_path):
    day1 = skill_body(captured="2026-08-10", source="repo@session-1")
    day2 = skill_body(captured="2026-08-11", source="repo@session-2")
    assert day1 != day2
    staging.decline(tmp_path, make(body=day1), "not institutional knowledge")
    assert staging.is_declined(tmp_path, day2)


def test_candidate_id_ignores_capture_stamps(tmp_path):
    assert candidate_id("fact", "t", fact_body(verified="2026-08-10")) == candidate_id(
        "fact", "t", fact_body(verified="2026-08-11")
    )
    assert candidate_id(
        "skill", "t", skill_body(captured="2026-08-10", source="repo@session-1")
    ) == candidate_id(
        "skill", "t", skill_body(captured="2026-08-11", source="repo@session-2")
    )


def test_candidate_id_still_separates_dates_that_are_content(tmp_path):
    # Only mneme's own stamps are ignored. A date the fact *states* is knowledge, and two
    # facts naming different cutover dates must stay two distinct candidates.
    sept = fact_body(text="Cutover happens on 2026-09-01")
    octo = fact_body(text="Cutover happens on 2026-10-01")
    assert candidate_id("fact", "t", sept) != candidate_id("fact", "t", octo)
    staging.decline(tmp_path, make(type="fact", body=sept), "not useful")
    assert not staging.is_declined(tmp_path, octo)


def test_provenance_with_newlines_cannot_forge_frontmatter(tmp_path):
    cand = make(provenance={"note": "x: y\nid: hacked-id\nstatus: quarantined"})
    staging.write_candidate(tmp_path, cand)
    loaded = staging.load_candidates(tmp_path)[0]
    assert loaded.id == cand.id
    assert loaded.status == "staged"
    assert loaded.provenance == cand.provenance


def test_unparseable_candidate_file_names_itself(tmp_path):
    paths.ensure_layout(tmp_path)
    bad = paths.staging_dir(tmp_path) / "bad.md"
    bad.write_text("---\n???\n---\nbody\n", encoding="utf-8")
    with pytest.raises(MnemeError, match="bad.md"):
        staging.load_candidates(tmp_path)
