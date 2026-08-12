from mneme_core import staging
from mneme_core.staging import Candidate, candidate_id


def make(**kw):
    body = kw.pop("body", "- [gotcha] Fact text #x (verified: 2026-08-11)\n")
    defaults = dict(
        id=candidate_id("fact", "acme-knowledge", body),
        type="fact",
        edit="new",
        target="acme-knowledge",
        body=body,
    )
    defaults.update(kw)
    return Candidate(**defaults)


def test_new_fields_default_empty():
    cand = make()
    assert cand.topic == ""
    assert cand.similar_to == ""
    assert cand.boundary_warning == ""


def test_round_trip_with_metadata(tmp_path):
    cand = make(
        topic="staging-env",
        similar_to="facts/staging-env#staging-db-resets-nightly-at-04",
        boundary_warning="target 'public-kb' is public but source 'acme-knowledge' is internal",
    )
    staging.write_candidate(tmp_path, cand)
    loaded = staging.load_candidates(tmp_path)[0]
    assert loaded == cand


def test_legacy_candidate_without_new_keys_loads(tmp_path):
    cand = make()
    path = staging.write_candidate(tmp_path, cand)
    text = path.read_text(encoding="utf-8")
    stripped = "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith(("topic:", "similar-to:", "boundary-warning:"))
    ) + "\n"
    path.write_text(stripped, encoding="utf-8")
    loaded = staging.load_candidates(tmp_path)[0]
    assert loaded.topic == ""
    assert loaded.similar_to == ""
    assert loaded.boundary_warning == ""
