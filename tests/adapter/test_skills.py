from pathlib import Path

from mneme_core import lint, units

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"

IMPERATIVE = ["capture", "status", "verify", "adopt", "register"]


def test_imperative_skills_exist_and_lint_clean():
    for name in IMPERATIVE:
        d = SKILLS_DIR / name
        assert (d / "SKILL.md").exists(), name
        assert lint.lint_skill(d) == [], name


def test_imperative_skills_are_user_only():
    for name in IMPERATIVE:
        meta, _ = units.parse_frontmatter(
            (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        )
        assert str(meta.get("disable-model-invocation", "")).lower() == "true", name


def test_capture_mentions_flag_command():
    body = (SKILLS_DIR / "capture" / "SKILL.md").read_text(encoding="utf-8")
    assert "mneme flag" in body
    assert "$ARGUMENTS" in body


def test_register_covers_clone_and_adopt():
    body = (SKILLS_DIR / "register" / "SKILL.md").read_text(encoding="utf-8")
    assert "registry add" in body
    assert "--clone" in body
    assert "adopt" in body


def test_share_and_new_skills():
    for name in ("share", "new"):
        d = SKILLS_DIR / name
        assert lint.lint_skill(d) == [], name
        meta, body = units.parse_frontmatter((d / "SKILL.md").read_text(encoding="utf-8"))
        assert str(meta.get("disable-model-invocation", "")).lower() == "true", name


def test_share_flow_covers_the_gate():
    body = (SKILLS_DIR / "share" / "SKILL.md").read_text(encoding="utf-8")
    for token in ("share list", "share diff", "share apply", "decline", "boundary", "QUARANTINED"):
        assert token in body, token


def test_new_interviews_before_creating():
    body = (SKILLS_DIR / "new" / "SKILL.md").read_text(encoding="utf-8")
    assert "MNEME.md" in body
    assert "mneme new" in body
    assert "scope" in body.lower()
