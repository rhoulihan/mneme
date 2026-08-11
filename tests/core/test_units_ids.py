import pytest

from mneme_core.errors import MnemeError
from mneme_core.units import (
    FACT_CATEGORIES,
    content_hash,
    fact_unit_id,
    normalize_topic_key,
    parse_bullet_line,
    parse_fact_bullets,
    skill_unit_id,
)


def test_fact_categories():
    assert FACT_CATEGORIES == {"decision", "constraint", "gotcha", "runbook-note", "reference"}


def test_parse_full_bullet():
    line = "- [constraint] Staging DB resets nightly at 04:00 UTC #staging #db (verified: 2026-08-11)"
    b = parse_bullet_line(line, 7)
    assert b.category == "constraint"
    assert b.text == "Staging DB resets nightly at 04:00 UTC"
    assert b.tags == ["staging", "db"]
    assert b.verified == "2026-08-11"
    assert b.line_no == 7


def test_parse_minimal_bullet():
    b = parse_bullet_line("- [gotcha] v2 API truncates batch writes", 1)
    assert b.category == "gotcha"
    assert b.tags == []
    assert b.verified is None


def test_malformed_bullet_raises():
    with pytest.raises(MnemeError):
        parse_bullet_line("- [gotcha no closing bracket", 3)


def test_parse_fact_bullets_skips_non_bullet_lines():
    body = "## Topic\n\n- [decision] Use Oracle 26ai #db\nprose line\n- [gotcha] Thing #x\n"
    bullets = parse_fact_bullets(body)
    assert [b.category for b in bullets] == ["decision", "gotcha"]
    assert bullets[0].line_no == 3
    assert bullets[1].line_no == 5


def test_normalize_topic_key_first_six_words():
    key = normalize_topic_key("Staging DB resets nightly at 04:00 UTC every day")
    assert key == "staging-db-resets-nightly-at-04"


def test_topic_key_property_matches_function():
    b = parse_bullet_line("- [constraint] Staging DB resets nightly #staging", 1)
    assert b.topic_key == normalize_topic_key("Staging DB resets nightly")


def test_content_hash_normalizes_whitespace():
    assert content_hash("a  b\nc") == content_hash("a b c")
    assert len(content_hash("x")) == 12
    assert content_hash("x") != content_hash("y")


def test_unit_ids():
    assert skill_unit_id("deploy-widget") == "skills/deploy-widget"
    assert fact_unit_id("staging-env", "Staging DB resets nightly") == (
        "facts/staging-env#staging-db-resets-nightly"
    )
