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


def apply_fact(repo: Path, cand: Candidate) -> str:
    if cand.edit == "new" and not cand.topic:
        raise MnemeError(f"candidate {cand.id}: fact candidate has no topic")
    line = cand.body.strip()
    bullet = units.parse_bullet_line(line, 1)

    if cand.edit == "new":
        path = repo / "facts" / f"{cand.topic}.md"
        if path.exists():
            meta, body = units.parse_frontmatter(path.read_text(encoding="utf-8-sig"))
        else:
            meta, body = {"topic": cand.topic}, ""
        for n, existing in enumerate(body.splitlines(), start=1):
            if existing.startswith("- ["):
                if units.parse_bullet_line(existing, n).topic_key == bullet.topic_key:
                    raise MnemeError(
                        f"candidate {cand.id}: topic key '{bullet.topic_key}' already exists"
                        f" in facts/{cand.topic}.md — expected an update edit"
                    )
        new_body = body.rstrip("\n")
        new_body = (new_body + "\n" if new_body else "") + line + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(units.serialize_frontmatter(meta, new_body), encoding="utf-8")
        return f"facts/{cand.topic}#{bullet.topic_key} (new fact)"

    if "#" not in cand.target_unit or not cand.target_unit.startswith("facts/"):
        raise MnemeError(f"candidate {cand.id}: malformed fact target_unit {cand.target_unit!r}")
    file_part, key = cand.target_unit.removeprefix("facts/").split("#", 1)
    path = repo / "facts" / f"{file_part}.md"
    if not path.exists():
        raise MnemeError(f"candidate {cand.id}: update target file {path.name} not found")
    meta, body = units.parse_frontmatter(path.read_text(encoding="utf-8-sig"))
    out_lines: list[str] = []
    replaced = False
    for n, existing in enumerate(body.splitlines(), start=1):
        if not replaced and existing.startswith("- ["):
            try:
                if units.parse_bullet_line(existing, n).topic_key == key:
                    out_lines.append(line)
                    replaced = True
                    continue
            except MnemeError:
                pass
        out_lines.append(existing)
    if not replaced:
        raise MnemeError(
            f"candidate {cand.id}: no bullet with topic key '{key}' in facts/{file_part}.md"
        )
    path.write_text(
        units.serialize_frontmatter(meta, "\n".join(out_lines) + "\n"), encoding="utf-8"
    )
    return f"facts/{file_part}#{key} (updated fact)"
