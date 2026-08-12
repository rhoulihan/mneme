from pathlib import Path

import pytest

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


def test_retrieval_skill_is_model_invocable():
    d = SKILLS_DIR / "retrieval"
    assert lint.lint_skill(d) == []
    meta, body = units.parse_frontmatter((d / "SKILL.md").read_text(encoding="utf-8"))
    assert "disable-model-invocation" not in meta
    assert "mneme search" in body
    assert "mneme db query" in body


def frontmatter_block(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0].strip() == "---", path
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    return lines[1:end]


def test_frontmatter_parses_as_real_yaml():
    # Claude Code parses SKILL.md frontmatter with a real YAML parser, not mneme's
    # units.parse_frontmatter. An unquoted `argument-hint: [a] [b]` is a flow
    # sequence followed by a stray token: the whole block fails to parse and every
    # field is dropped at load time — including disable-model-invocation, which is
    # the only thing keeping these skills user-only.
    yaml = pytest.importorskip("yaml")
    for d in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        meta = yaml.safe_load("\n".join(frontmatter_block(d / "SKILL.md")))
        assert isinstance(meta, dict), d.name
        assert meta.get("name") == d.name
        assert meta.get("description")
        if "argument-hint" in meta:
            assert isinstance(meta["argument-hint"], str), d.name
        if d.name in IMPERATIVE or d.name in ("share", "new"):
            assert meta.get("disable-model-invocation") is True, d.name


def test_argument_hints_are_quoted_scalars():
    # The YAML check above needs PyYAML; this one holds even without it.
    for d in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        for line in frontmatter_block(d / "SKILL.md"):
            if line.startswith("argument-hint:"):
                value = line.split(":", 1)[1].strip()
                assert value.startswith('"') and value.endswith('"'), d.name
