"""Canonical unit rendering — proposals in, spec-valid units out (spec §5.2–5.3)."""
from __future__ import annotations

import re

from . import units
from .errors import MnemeError

_TAG_RE = re.compile(r"^[\w-]+$")
_MAX_DESCRIPTION = 1024


def render_skill_unit(
    name: str,
    description: str,
    procedure: str,
    failure_pattern: str,
    *,
    source: str,
    captured: str,
) -> str:
    if not units.KEBAB_RE.match(name):
        raise MnemeError(f"skill name must be kebab-case: {name!r}")
    if not description.strip():
        raise MnemeError("skill description must not be empty")
    if len(description) > _MAX_DESCRIPTION:
        raise MnemeError(f"skill description exceeds {_MAX_DESCRIPTION} chars")
    if not procedure.strip():
        raise MnemeError("skill procedure must not be empty")
    if not failure_pattern.strip():
        raise MnemeError("skill failure_pattern must not be empty")
    meta = {
        "name": name,
        "description": description.strip(),
        "metadata": {
            "mneme-type": "skill",
            "mneme-source": source,
            "mneme-captured": captured,
            "mneme-last-verified": captured,
        },
    }
    body = (
        f"# {name}\n\n"
        f"## Procedure\n\n{procedure.strip()}\n\n"
        f"## Failure pattern\n\n{failure_pattern.strip()}\n"
    )
    return units.serialize_frontmatter(meta, body)


def render_fact_bullet(
    category: str, text: str, tags: list[str], *, verified: str
) -> str:
    if category not in units.FACT_CATEGORIES:
        raise MnemeError(f"unknown fact category: {category!r}")
    folded = " ".join(text.split())
    if not folded:
        raise MnemeError("fact text must not be empty")
    for tag in tags:
        if not _TAG_RE.match(tag):
            raise MnemeError(f"invalid tag: {tag!r}")
    tag_part = "".join(f" #{t}" for t in tags)
    line = f"- [{category}] {folded}{tag_part} (verified: {verified})"
    try:
        units.parse_bullet_line(line, 1)
    except MnemeError:
        raise MnemeError(f"fact text does not survive bullet grammar: {folded!r}")
    return line
