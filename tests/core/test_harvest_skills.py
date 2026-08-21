import pytest

from mneme_core import compose, harvest
from mneme_core.errors import MnemeError
from mneme_core.staging import Candidate, candidate_id


def _as_kb(repo):
    """A repo whose skills/ mneme maintains — otherwise `apply_skill` refuses on MODE
    before it ever reaches the path check these tests exist to exercise."""
    (repo / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (repo / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "kb", "version": "0.1.0"}\n', encoding="utf-8"
    )
    return repo

def skill_body(name="deploy-widget", description="Use when deploying widgets"):
    return compose.render_skill_unit(
        name, description, "1. steps", "what failed first",
        source="demo@s1", captured="2026-08-11",
    )


def make_candidate(body, edit="new", target_unit=""):
    return Candidate(
        id=candidate_id("skill", "acme-knowledge", body),
        type="skill", edit=edit, target="acme-knowledge",
        body=body, target_unit=target_unit,
    )


def test_apply_new_skill(tmp_path):
    _as_kb(tmp_path)
    body = skill_body()
    line = harvest.apply_skill(tmp_path, make_candidate(body))
    written = tmp_path / "skills" / "deploy-widget" / "SKILL.md"
    assert written.read_text(encoding="utf-8") == body
    assert line == "skills/deploy-widget (new skill)"


def test_apply_new_skill_conflict(tmp_path):
    _as_kb(tmp_path)
    body = skill_body()
    harvest.apply_skill(tmp_path, make_candidate(body))
    with pytest.raises(MnemeError):
        harvest.apply_skill(tmp_path, make_candidate(body))


def test_apply_update_replaces(tmp_path):
    _as_kb(tmp_path)
    harvest.apply_skill(tmp_path, make_candidate(skill_body()))
    new_body = skill_body(description="Use when deploying widgets after the LB fix")
    line = harvest.apply_skill(
        tmp_path,
        make_candidate(new_body, edit="update", target_unit="skills/deploy-widget"),
    )
    assert line == "skills/deploy-widget (updated skill)"
    text = (tmp_path / "skills" / "deploy-widget" / "SKILL.md").read_text(encoding="utf-8")
    assert "after the LB fix" in text


def test_apply_update_missing_target(tmp_path):
    _as_kb(tmp_path)
    with pytest.raises(MnemeError):
        harvest.apply_skill(
            tmp_path,
            make_candidate(skill_body(), edit="update", target_unit="skills/deploy-widget"),
        )


def test_apply_update_name_mismatch(tmp_path):
    _as_kb(tmp_path)
    harvest.apply_skill(tmp_path, make_candidate(skill_body()))
    other = skill_body(name="other-skill")
    with pytest.raises(MnemeError):
        harvest.apply_skill(
            tmp_path, make_candidate(other, edit="update", target_unit="skills/deploy-widget")
        )
