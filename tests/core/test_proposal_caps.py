import json

import pytest

from mneme_core import proposals
from mneme_core.errors import MnemeError


def fact_entry(**kw):
    entry = dict(
        type="fact", edit="new", target="acme-knowledge",
        topic="staging-env", category="constraint",
        text="DB resets nightly", tags=["staging"],
        confidence=0.7, rationale="observed",
    )
    entry.update(kw)
    return entry


def skill_entry(**kw):
    entry = dict(
        type="skill", edit="new", target="acme-knowledge",
        name="deploy-widget", description="Use when deploying widgets",
        procedure="Steps.", failure_pattern="What failed.",
        confidence=0.8, rationale="verified",
    )
    entry.update(kw)
    return entry


def parse(entries):
    return proposals.parse_proposals(json.dumps({"proposals": entries}))


def test_document_cap():
    with pytest.raises(MnemeError):
        parse([fact_entry() for _ in range(proposals.MAX_PROPOSALS + 1)])
    valid, errors = parse([fact_entry()] * proposals.MAX_PROPOSALS)
    assert errors == []
    assert len(valid) == proposals.MAX_PROPOSALS


@pytest.mark.parametrize(
    "entry_kwargs, field",
    [
        ({"rationale": "x" * (proposals.MAX_RATIONALE + 1)}, "rationale"),
        ({"text": "x" * (proposals.MAX_FACT_TEXT + 1)}, "text"),
        ({"tags": ["t"] * (proposals.MAX_TAGS + 1)}, "tags"),
        ({"target": "t" * (proposals.MAX_TARGET + 1)}, "target"),
    ],
)
def test_fact_field_caps(entry_kwargs, field):
    valid, errors = parse([fact_entry(**entry_kwargs)])
    assert valid == []
    assert len(errors) == 1
    assert field in errors[0]


@pytest.mark.parametrize(
    "entry_kwargs, field",
    [
        ({"procedure": "x" * (proposals.MAX_PROCEDURE + 1)}, "procedure"),
        ({"failure_pattern": "x" * (proposals.MAX_FAILURE_PATTERN + 1)}, "failure_pattern"),
        (
            {"edit": "update", "target_unit": "skills/" + "x" * proposals.MAX_TARGET_UNIT},
            "target_unit",
        ),
    ],
)
def test_skill_field_caps(entry_kwargs, field):
    valid, errors = parse([skill_entry(**entry_kwargs)])
    assert valid == []
    assert len(errors) == 1
    assert field in errors[0]


def test_boundary_values_pass():
    valid, errors = parse(
        [
            fact_entry(text="x" * proposals.MAX_FACT_TEXT, rationale="r" * proposals.MAX_RATIONALE),
            skill_entry(procedure="p" * proposals.MAX_PROCEDURE),
        ]
    )
    assert errors == []
    assert len(valid) == 2
