import pytest

from mneme_core import compose, lint, units
from mneme_core.errors import MnemeError


def test_skill_unit_is_lint_clean(tmp_path):
    text = compose.render_skill_unit(
        "deploy-widget",
        "Use when deploying the widget service after a failed cutover",
        "1. Run preflight.\n2. Cut over blue-green.",
        "Naive restart loops forever because the LB caches the dead target.",
        source="acme/app@session-42",
        captured="2026-08-11",
    )
    d = tmp_path / "skills" / "deploy-widget"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    assert lint.lint_skill(d) == []
    meta, body = units.parse_frontmatter(text)
    assert meta["name"] == "deploy-widget"
    assert meta["metadata"]["mneme-type"] == "skill"
    assert meta["metadata"]["mneme-captured"] == "2026-08-11"
    assert meta["metadata"]["mneme-last-verified"] == "2026-08-11"
    assert meta["metadata"]["mneme-source"] == "acme/app@session-42"
    assert "## Procedure" in body
    assert "## Failure pattern" in body
    assert "LB caches the dead target" in body


def test_fact_bullet_round_trips():
    line = compose.render_fact_bullet(
        "constraint",
        "Staging DB resets nightly at 04:00 UTC",
        ["staging", "db"],
        verified="2026-08-11",
    )
    b = units.parse_bullet_line(line, 1)
    assert b.category == "constraint"
    assert b.text == "Staging DB resets nightly at 04:00 UTC"
    assert b.tags == ["staging", "db"]
    assert b.verified == "2026-08-11"


def test_fact_bullet_folds_multiline_text():
    line = compose.render_fact_bullet(
        "gotcha", "line one\n   line two\t tabbed", [], verified="2026-08-11"
    )
    b = units.parse_bullet_line(line, 1)
    assert b.text == "line one line two tabbed"


def test_fact_bullet_no_tags_no_trailing_gap():
    line = compose.render_fact_bullet("reference", "See runbook", [], verified="2026-08-11")
    assert line == "- [reference] See runbook (verified: 2026-08-11)"


def test_invalid_inputs_raise():
    with pytest.raises(MnemeError):
        compose.render_skill_unit("Bad_Name", "d", "p", "f", source="s", captured="2026-08-11")
    with pytest.raises(MnemeError):
        compose.render_skill_unit("ok-name", "", "p", "f", source="s", captured="2026-08-11")
    with pytest.raises(MnemeError):
        compose.render_fact_bullet("bogus", "text", [], verified="2026-08-11")
    with pytest.raises(MnemeError):
        compose.render_fact_bullet("gotcha", "", [], verified="2026-08-11")
    with pytest.raises(MnemeError):
        compose.render_fact_bullet("gotcha", "text", ["bad tag!"], verified="2026-08-11")


def test_trailing_newline_never_passes_a_gate():
    # `$`-anchored regexes match before a trailing newline; these must not slip through.
    with pytest.raises(MnemeError):
        compose.render_skill_unit(
            "deploy-widget\n", "d", "p", "f", source="s", captured="2026-08-11"
        )
    with pytest.raises(MnemeError):
        compose.render_fact_bullet("gotcha", "text here", ["staging\n"], verified="2026-08-11")


def test_fact_bullet_is_always_one_line(tmp_path):
    line = compose.render_fact_bullet(
        "gotcha", "text here", ["staging"], verified="2026-08-11"
    )
    assert "\n" not in line and "\r" not in line
    # Written to a facts file and re-read line-by-line, the fields must survive intact.
    f = tmp_path / "facts.md"
    f.write_text(f"---\ntopic: t\n---\n\n{line}\n", encoding="utf-8")
    _meta, body = units.parse_frontmatter(f.read_text(encoding="utf-8"))
    bullets = units.parse_fact_bullets(body)
    assert len(bullets) == 1
    assert bullets[0].verified == "2026-08-11"
    assert bullets[0].tags == ["staging"]
    assert lint.lint_fact_file(f) == []


def test_fact_text_cannot_smuggle_fields():
    with pytest.raises(MnemeError):
        compose.render_fact_bullet("gotcha", "evil #smuggled", [], verified="2026-08-11")
    # A `(verified: ...)` inside the text stays inside the text — the real trailing date
    # wins — so this one is faithful and is allowed through.
    line = compose.render_fact_bullet(
        "gotcha", "evil (verified: 2001-01-01)", [], verified="2026-08-11"
    )
    b = units.parse_bullet_line(line, 1)
    assert b.text == "evil (verified: 2001-01-01)"
    assert b.verified == "2026-08-11"


def test_verified_must_be_an_iso_date():
    with pytest.raises(MnemeError):
        compose.render_fact_bullet("gotcha", "text", [], verified="yesterday")
    with pytest.raises(MnemeError):
        compose.render_fact_bullet(
            "gotcha", "text", [], verified="2026-08-11\n- [gotcha] injected"
        )
