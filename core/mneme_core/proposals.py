"""Distiller proposal parsing — untrusted structured data in, validated objects out."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import staging, units
from .errors import MnemeError

UNASSIGNED = staging.UNASSIGNED
_MAX_DESCRIPTION = 1024
# Matched with `fullmatch`, never `match` + `$`: `$` matches before a trailing newline,
# which would let `"staging\n"` through as a tag and smuggle a line break into a fact
# bullet that must stay on one line.
_TAG_RE = re.compile(r"[\w-]+")


@dataclass
class Proposal:
    type: str
    edit: str
    target: str
    confidence: float
    rationale: str
    target_unit: str = ""
    name: str = ""
    description: str = ""
    procedure: str = ""
    failure_pattern: str = ""
    topic: str = ""
    category: str = ""
    text: str = ""
    tags: list[str] = field(default_factory=list)


def _validate(entry: dict) -> Proposal:
    if not isinstance(entry, dict):
        raise MnemeError("entry is not an object")
    type_ = str(entry.get("type", ""))
    if type_ not in staging.TYPES:
        raise MnemeError(f"type must be one of {sorted(staging.TYPES)}: {type_!r}")
    edit = str(entry.get("edit", "new"))
    if edit not in staging.EDITS:
        raise MnemeError(f"edit must be one of {sorted(staging.EDITS)}: {edit!r}")
    target_unit = str(entry.get("target_unit", ""))
    if edit == "update" and not target_unit:
        raise MnemeError("update proposals must set target_unit")
    target = str(entry.get("target") or UNASSIGNED)
    raw_conf = entry.get("confidence", 0.5)
    try:
        confidence = float(raw_conf)
    except (TypeError, ValueError):
        raise MnemeError(f"confidence is not a number: {raw_conf!r}")
    if not 0.0 <= confidence <= 1.0:
        raise MnemeError(f"confidence out of range [0, 1]: {confidence}")
    rationale = str(entry.get("rationale", ""))

    p = Proposal(
        type=type_, edit=edit, target=target, confidence=confidence,
        rationale=rationale, target_unit=target_unit,
    )
    if type_ == "skill":
        p.name = str(entry.get("name", ""))
        p.description = str(entry.get("description", ""))
        p.procedure = str(entry.get("procedure", ""))
        p.failure_pattern = str(entry.get("failure_pattern", ""))
        if not units.KEBAB_RE.match(p.name):
            raise MnemeError(f"skill name must be kebab-case: {p.name!r}")
        if not p.description.strip():
            raise MnemeError("skill description must not be empty")
        if len(p.description) > _MAX_DESCRIPTION:
            raise MnemeError(f"skill description exceeds {_MAX_DESCRIPTION} chars")
        if not p.procedure.strip():
            raise MnemeError("skill procedure must not be empty")
        if not p.failure_pattern.strip():
            raise MnemeError("skill failure_pattern must not be empty")
    else:
        p.topic = str(entry.get("topic", ""))
        p.category = str(entry.get("category", ""))
        p.text = str(entry.get("text", ""))
        raw_tags = entry.get("tags", [])
        if not isinstance(raw_tags, list):
            raise MnemeError("tags must be a list")
        p.tags = [str(t) for t in raw_tags]
        if not units.KEBAB_RE.match(p.topic):
            raise MnemeError(f"fact topic must be kebab-case: {p.topic!r}")
        if p.category not in units.FACT_CATEGORIES:
            raise MnemeError(f"unknown fact category: {p.category!r}")
        if not p.text.strip():
            raise MnemeError("fact text must not be empty")
        for tag in p.tags:
            if not _TAG_RE.fullmatch(tag):
                raise MnemeError(f"invalid tag: {tag!r}")
    return p


def parse_proposals(raw: str) -> tuple[list[Proposal], list[str]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise MnemeError(f"proposals are not valid JSON: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        raise MnemeError("proposals document must be an object with a 'proposals' list")
    valid: list[Proposal] = []
    errors: list[str] = []
    for i, entry in enumerate(data["proposals"]):
        try:
            valid.append(_validate(entry))
        except MnemeError as e:
            errors.append(f"proposal {i}: {e}")
    return valid, errors
