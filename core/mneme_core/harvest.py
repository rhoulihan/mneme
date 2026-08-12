"""Applying approved candidates to knowledge-repo clones (spec §7.3)."""
from __future__ import annotations

from pathlib import Path

from . import units
from .errors import MnemeError
from .staging import Candidate


def _skill_name(cand: Candidate) -> str:
    meta, _body = units.parse_frontmatter(cand.body)
    name = str(meta.get("name", ""))
    if not name:
        raise MnemeError(f"candidate {cand.id}: skill body has no frontmatter name")
    return name


def apply_skill(repo: Path, cand: Candidate) -> str:
    name = _skill_name(cand)
    skill_md = repo / "skills" / name / "SKILL.md"
    if cand.edit == "new":
        if skill_md.exists():
            raise MnemeError(
                f"candidate {cand.id}: skills/{name} already exists — expected an update edit"
            )
        skill_md.parent.mkdir(parents=True, exist_ok=True)
        skill_md.write_text(cand.body, encoding="utf-8")
        return f"skills/{name} (new skill)"
    expected = cand.target_unit.removeprefix("skills/")
    if name != expected:
        raise MnemeError(
            f"candidate {cand.id}: body names skill {name!r} but targets {cand.target_unit!r}"
        )
    if not skill_md.exists():
        raise MnemeError(f"candidate {cand.id}: update target {cand.target_unit} not found")
    skill_md.write_text(cand.body, encoding="utf-8")
    return f"skills/{name} (updated skill)"
