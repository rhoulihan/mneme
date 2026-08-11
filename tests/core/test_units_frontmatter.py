import pytest

from mneme_core.errors import MnemeError
from mneme_core.units import parse_frontmatter, serialize_frontmatter


def test_no_frontmatter_returns_empty_meta_and_full_text():
    meta, body = parse_frontmatter("just a body\n")
    assert meta == {}
    assert body == "just a body\n"


def test_flat_and_quoted_values():
    text = '---\nname: deploy-widget\ntitle: "a: colon value"\n---\nbody here'
    meta, body = parse_frontmatter(text)
    assert meta == {"name": "deploy-widget", "title": "a: colon value"}
    assert body == "body here"


def test_nested_map_one_level():
    text = "---\nmetadata:\n  mneme-type: skill\n  mneme-captured: 2026-08-11\n---\n"
    meta, _ = parse_frontmatter(text)
    assert meta["metadata"] == {"mneme-type": "skill", "mneme-captured": "2026-08-11"}


def test_list_of_scalars():
    text = "---\ntags:\n  - alpha\n  - beta\n---\n"
    meta, _ = parse_frontmatter(text)
    assert meta["tags"] == ["alpha", "beta"]


def test_folded_and_literal_block_scalars():
    folded = "---\ndescription: >\n  line one\n  line two\n---\n"
    meta, _ = parse_frontmatter(folded)
    assert meta["description"] == "line one line two"

    literal = "---\nnotes: |\n  line one\n  line two\n---\n"
    meta, _ = parse_frontmatter(literal)
    assert meta["notes"] == "line one\nline two"


def test_unterminated_frontmatter_raises():
    with pytest.raises(MnemeError):
        parse_frontmatter("---\nname: x\nno closing delim")


def test_unparseable_line_raises():
    with pytest.raises(MnemeError):
        parse_frontmatter("---\n???\n---\n")


def test_round_trip():
    meta = {
        "name": "deploy-widget",
        "description": "Use when deploying widget-service",
        "metadata": {"mneme-type": "skill"},
        "tags": ["alpha", "beta"],
        "notes": "line one\nline two",
    }
    body = "# Procedure\n\nSteps here.\n"
    text = serialize_frontmatter(meta, body)
    meta2, body2 = parse_frontmatter(text)
    assert meta2 == meta
    assert body2 == body


def test_round_trip_survives_newlines_in_nested_values_and_lists():
    # A newline written raw would let continuation lines pose as top-level keys.
    meta = {
        "id": "real-id",
        "provenance": {"note": "x: y\nid: hacked-id\nstatus: quarantined"},
        "tags": ["line one\nline two"],
    }
    meta2, _ = parse_frontmatter(serialize_frontmatter(meta, "body\n"))
    assert meta2 == meta


def test_round_trip_survives_quotes_backslashes_and_padding():
    meta = {
        "a": "he said \"hi\"",
        "b": "C:\\path\\new",
        "c": "  padded  ",
        "d": "",
        "e": "tab\there",
    }
    meta2, _ = parse_frontmatter(serialize_frontmatter(meta, "body\n"))
    assert meta2 == meta


def test_unserializable_key_raises():
    with pytest.raises(MnemeError):
        serialize_frontmatter({"bad key": "v"}, "body\n")
    with pytest.raises(MnemeError):
        serialize_frontmatter({"provenance": {"bad\nkey": "v"}}, "body\n")
