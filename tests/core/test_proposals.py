import json

import pytest

from mneme_core import proposals
from mneme_core.errors import MnemeError


def skill_entry(**kw):
    entry = dict(
        type="skill", edit="new", target="acme-knowledge",
        name="deploy-widget", description="Use when deploying widgets",
        procedure="Steps.", failure_pattern="What failed first.",
        confidence=0.8, rationale="verified in session",
    )
    entry.update(kw)
    return entry


def fact_entry(**kw):
    entry = dict(
        type="fact", edit="new", target="acme-knowledge",
        topic="staging-env", category="constraint",
        text="DB resets nightly", tags=["staging"],
        confidence=0.7, rationale="observed twice",
    )
    entry.update(kw)
    return entry


def parse(entries):
    return proposals.parse_proposals(json.dumps({"proposals": entries}))


def test_valid_skill_and_fact():
    valid, errors = parse([skill_entry(), fact_entry()])
    assert errors == []
    assert [p.type for p in valid] == ["skill", "fact"]
    assert valid[0].name == "deploy-widget"
    assert valid[1].tags == ["staging"]


def test_bad_entries_reported_independently():
    valid, errors = parse(
        [
            skill_entry(name="Bad_Name"),
            fact_entry(),
            fact_entry(category="bogus"),
            skill_entry(edit="update"),  # missing target_unit
        ]
    )
    assert len(valid) == 1
    assert len(errors) == 3
    assert errors[0].startswith("proposal 0:")
    assert errors[1].startswith("proposal 2:")
    assert errors[2].startswith("proposal 3:")


def test_defaults_applied():
    valid, errors = parse([{k: v for k, v in fact_entry().items() if k not in ("target", "confidence")}])
    assert errors == []
    assert valid[0].target == "unassigned"
    assert valid[0].confidence == 0.5


def test_confidence_bounds():
    _, errors = parse([fact_entry(confidence=1.5)])
    assert len(errors) == 1
    _, errors = parse([fact_entry(confidence="not-a-number")])
    assert len(errors) == 1


def test_non_json_raises():
    with pytest.raises(MnemeError):
        proposals.parse_proposals("not json at all")
    with pytest.raises(MnemeError):
        proposals.parse_proposals(json.dumps({"nope": []}))


def test_update_with_target_unit_ok():
    valid, errors = parse([fact_entry(edit="update", target_unit="facts/staging-env#db-resets")])
    assert errors == []
    assert valid[0].target_unit == "facts/staging-env#db-resets"


def test_deeply_nested_json_raises_mneme_error_not_recursion_error():
    # json's C scanner raises RecursionError, which is not a JSONDecodeError. Hostile
    # LLM output must leave this function as a MnemeError like every other bad document.
    raw = '{"proposals": ' + "[" * 60000 + "]" * 60000 + "}"
    with pytest.raises(MnemeError, match="nested too deeply"):
        proposals.parse_proposals(raw)


def test_recursion_error_while_validating_rejects_only_that_proposal(monkeypatch):
    # A value nested just under the parser's limit survives json.loads but can blow the
    # stack when _validate stringifies it: that is one rejection, not a dead run.
    def boom(entry):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(proposals, "_validate", boom)
    valid, errors = proposals.parse_proposals(json.dumps({"proposals": [fact_entry()]}))
    assert valid == []
    assert errors == ["proposal 0: value is nested too deeply to validate"]
