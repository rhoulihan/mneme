import pytest

from mneme_core import compose, harvest, units
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id


def bullet(text="Staging DB resets nightly at 04:00 UTC", category="constraint"):
    return compose.render_fact_bullet(category, text, ["staging"], verified="2026-08-11")


def make_candidate(body, topic="staging-env", edit="new", target_unit=""):
    return Candidate(
        id=candidate_id("fact", "acme-knowledge", body),
        type="fact", edit=edit, target="acme-knowledge",
        body=body, topic=topic, target_unit=target_unit,
    )


def test_apply_new_fact_creates_file(tmp_path):
    line = harvest.apply_fact(tmp_path, make_candidate(bullet()))
    text = (tmp_path / "facts" / "staging-env.md").read_text(encoding="utf-8")
    meta, body = units.parse_frontmatter(text)
    assert meta["topic"] == "staging-env"
    assert body.strip().startswith("- [constraint] Staging DB resets nightly")
    assert line == "facts/staging-env#staging-db-resets-nightly-at-04 (new fact)"


def test_apply_new_fact_appends_preserving_existing(tmp_path):
    harvest.apply_fact(tmp_path, make_candidate(bullet()))
    other = bullet(text="v2 API truncates batch writes over 500 items", category="gotcha")
    harvest.apply_fact(tmp_path, make_candidate(other))
    text = (tmp_path / "facts" / "staging-env.md").read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if l.startswith("- [")]
    assert len(lines) == 2
    assert "resets nightly" in lines[0]
    assert "truncates batch" in lines[1]


def test_apply_new_duplicate_topic_key_rejected(tmp_path):
    harvest.apply_fact(tmp_path, make_candidate(bullet()))
    same_key = bullet(text="Staging DB resets nightly at 04:00 UTC exactly")
    assert units.normalize_topic_key(
        "Staging DB resets nightly at 04:00 UTC exactly"
    ) == units.normalize_topic_key("Staging DB resets nightly at 04:00 UTC")
    with pytest.raises(MnemeError):
        harvest.apply_fact(tmp_path, make_candidate(same_key))


def test_apply_update_replaces_single_line(tmp_path):
    harvest.apply_fact(tmp_path, make_candidate(bullet()))
    harvest.apply_fact(
        tmp_path,
        make_candidate(
            bullet(text="v2 API truncates batch writes over 500 items", category="gotcha"),
        ),
    )
    new_line = compose.render_fact_bullet(
        "constraint", "Staging DB resets nightly at 03:00 UTC now", ["staging"],
        verified="2026-08-12",
    )
    result = harvest.apply_fact(
        tmp_path,
        make_candidate(
            new_line, edit="update",
            target_unit="facts/staging-env#staging-db-resets-nightly-at-04",
        ),
    )
    assert result.endswith("(updated fact)")
    text = (tmp_path / "facts" / "staging-env.md").read_text(encoding="utf-8")
    assert "03:00 UTC now" in text
    assert "04:00 UTC" not in text
    assert "truncates batch" in text  # untouched neighbor


def test_apply_update_missing_key_or_file(tmp_path):
    with pytest.raises(MnemeError):
        harvest.apply_fact(
            tmp_path,
            make_candidate(bullet(), edit="update", target_unit="facts/absent#nope"),
        )
    harvest.apply_fact(tmp_path, make_candidate(bullet()))
    with pytest.raises(MnemeError):
        harvest.apply_fact(
            tmp_path,
            make_candidate(bullet(), edit="update", target_unit="facts/staging-env#no-such-key"),
        )


def test_apply_fact_requires_topic(tmp_path):
    with pytest.raises(MnemeError):
        harvest.apply_fact(tmp_path, make_candidate(bullet(), topic=""))
