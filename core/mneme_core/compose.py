"""Canonical unit rendering — proposals in, spec-valid units out (spec §5.2–5.3)."""
from __future__ import annotations

import re

from . import units
from .errors import MnemeError

# Anchored with `fullmatch` below, never `re.match` + `$`: `$` matches before a trailing
# newline, which would let `"staging\n"` through as a tag and smuggle a line break into a
# bullet that must stay on one line.
_TAG_RE = re.compile(r"[\w-]+")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MAX_DESCRIPTION = units.MAX_DESCRIPTION


def render_skill_unit(
    name: str,
    description: str,
    procedure: str,
    failure_pattern: str,
    *,
    source: str,
    captured: str,
) -> str:
    if not units.KEBAB_RE.fullmatch(name):
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
    text = units.serialize_frontmatter(meta, body)
    # Valid by construction means proven, not assumed: read the unit back the way lint
    # will and refuse to emit anything whose frontmatter does not survive the round trip.
    try:
        parsed, _parsed_body = units.parse_frontmatter(text)
    except MnemeError as e:
        raise MnemeError(f"skill unit does not survive frontmatter round-trip: {e}") from None
    if parsed.get("name") != name or parsed.get("description") != description.strip():
        raise MnemeError("skill unit does not survive frontmatter round-trip")
    return text


def render_fact_bullet(
    category: str, text: str, tags: list[str], *, verified: str
) -> str:
    if category not in units.FACT_CATEGORIES:
        raise MnemeError(f"unknown fact category: {category!r}")
    folded = " ".join(text.split())
    if not folded:
        raise MnemeError("fact text must not be empty")
    for tag in tags:
        if not _TAG_RE.fullmatch(tag):
            raise MnemeError(f"invalid tag: {tag!r}")
    if not _ISO_DATE_RE.fullmatch(verified):
        raise MnemeError(f"verified must be an ISO date (YYYY-MM-DD): {verified!r}")
    tag_part = "".join(f" #{t}" for t in tags)
    line = f"- [{category}] {folded}{tag_part} (verified: {verified})"
    if "\n" in line or "\r" in line:
        raise MnemeError(f"fact bullet must be a single line: {line!r}")
    try:
        bullet = units.parse_bullet_line(line, 1)
    except MnemeError:
        raise MnemeError(f"fact text does not survive bullet grammar: {folded!r}") from None
    # Parsing is not enough — the parse must also mean what was asked for. Text carrying a
    # `#tag` or a `(verified: ...)` of its own re-reads as different fields, so reject it
    # rather than silently shipping a bullet whose fields drifted.
    if (bullet.category, bullet.text, bullet.tags, bullet.verified) != (
        category,
        folded,
        list(tags),
        verified,
    ):
        raise MnemeError(f"fact text does not survive bullet grammar: {folded!r}")
    return line
