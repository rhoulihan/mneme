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
    # A repo with neither layout on disk gets the canonical one.
    assert (tmp_path / units.FACTS_CANONICAL / "staging-env.md").exists()
    text = (units.facts_dir(tmp_path) / "staging-env.md").read_text(encoding="utf-8")
    meta, body = units.parse_frontmatter(text)
    assert meta["topic"] == "staging-env"
    assert body.strip().startswith("- [constraint] Staging DB resets nightly")
    assert line == "facts/staging-env#staging-db-resets-nightly-at-04 (new fact)"


def test_apply_new_and_update_in_a_legacy_layout(tmp_path):
    """A topic a legacy repo already carries is edited in place — same unit id.

    Appends and updates follow the file, wherever it lives, so one topic never becomes
    two files with half the bullets in each. (A topic the repo does NOT yet carry is a
    different case: that is a write, and writes are canonical — see the assertion at the
    end and `tests/core/test_facts_write_dir.py`.)
    """
    legacy = tmp_path / "facts"
    legacy.mkdir()
    (legacy / "staging-env.md").write_text(
        "---\ntopic: staging-env\n---\n"
        "- [gotcha] v2 API truncates batch writes over 500 items #api (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    line = harvest.apply_fact(tmp_path, make_candidate(bullet()))
    assert line == "facts/staging-env#staging-db-resets-nightly-at-04 (new fact)"
    assert not (tmp_path / units.FACTS_CANONICAL / "staging-env.md").exists()
    assert "resets nightly" in (legacy / "staging-env.md").read_text(encoding="utf-8")
    updated = compose.render_fact_bullet(
        "constraint", "Staging DB resets nightly at 03:00 UTC now", ["staging"],
        verified="2026-08-12",
    )
    result = harvest.apply_fact(
        tmp_path,
        make_candidate(
            updated, edit="update",
            target_unit="facts/staging-env#staging-db-resets-nightly-at-04",
        ),
    )
    assert result.endswith("(updated fact)")
    text = (tmp_path / "facts" / "staging-env.md").read_text(encoding="utf-8")
    assert "03:00 UTC now" in text
    assert "04:00 UTC" not in text

    # A topic this repo does not carry yet is created canonically, not beside the others.
    harvest.apply_fact(
        tmp_path,
        make_candidate(
            bullet(text="Nightly restores read from the 05:00 snapshot"), topic="restores"
        ),
    )
    assert (tmp_path / units.FACTS_CANONICAL / "restores.md").exists()
    assert not (legacy / "restores.md").exists()


def test_apply_new_fact_appends_preserving_existing(tmp_path):
    harvest.apply_fact(tmp_path, make_candidate(bullet()))
    other = bullet(text="v2 API truncates batch writes over 500 items", category="gotcha")
    harvest.apply_fact(tmp_path, make_candidate(other))
    text = (units.facts_dir(tmp_path) / "staging-env.md").read_text(encoding="utf-8")
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
    text = (units.facts_dir(tmp_path) / "staging-env.md").read_text(encoding="utf-8")
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
