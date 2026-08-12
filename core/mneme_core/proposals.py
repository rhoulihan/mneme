"""Distiller proposal parsing — untrusted structured data in, validated objects out."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import staging, units
from .errors import MnemeError

UNASSIGNED = staging.UNASSIGNED
_MAX_DESCRIPTION = 1024
# Caps on untrusted fields: proposals arrive as LLM output, so every unbounded string
# is a memory/ledger-bloat vector. Sizes are generous enough that honest content never
# trips them.
MAX_PROPOSALS = 100
MAX_RATIONALE = 2_000
MAX_PROCEDURE = 20_000
MAX_FAILURE_PATTERN = 20_000
MAX_FACT_TEXT = 2_000
MAX_TAGS = 20
MAX_TARGET = 100
MAX_TARGET_UNIT = 300
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


def _cap(value: str, limit: int, field: str) -> str:
    if len(value) > limit:
        raise MnemeError(f"{field} exceeds {limit} chars ({len(value)})")
    return value


def _validate(entry: dict) -> Proposal:
    if not isinstance(entry, dict):
        raise MnemeError("entry is not an object")
    type_ = str(entry.get("type", ""))
    if type_ not in staging.TYPES:
        raise MnemeError(f"type must be one of {sorted(staging.TYPES)}: {type_!r}")
    edit = str(entry.get("edit", "new"))
    if edit not in staging.EDITS:
        raise MnemeError(f"edit must be one of {sorted(staging.EDITS)}: {edit!r}")
    target_unit = _cap(str(entry.get("target_unit", "")), MAX_TARGET_UNIT, "target_unit")
    if edit == "update" and not target_unit:
        raise MnemeError("update proposals must set target_unit")
    target = _cap(str(entry.get("target") or UNASSIGNED), MAX_TARGET, "target")
    raw_conf = entry.get("confidence", 0.5)
    try:
        confidence = float(raw_conf)
    except (TypeError, ValueError):
        raise MnemeError(f"confidence is not a number: {raw_conf!r}")
    if not 0.0 <= confidence <= 1.0:
        raise MnemeError(f"confidence out of range [0, 1]: {confidence}")
    rationale = _cap(str(entry.get("rationale", "")), MAX_RATIONALE, "rationale")

    p = Proposal(
        type=type_, edit=edit, target=target, confidence=confidence,
        rationale=rationale, target_unit=target_unit,
    )
    if type_ == "skill":
        p.name = str(entry.get("name", ""))
        p.description = str(entry.get("description", ""))
        p.procedure = _cap(str(entry.get("procedure", "")), MAX_PROCEDURE, "procedure")
        p.failure_pattern = _cap(
            str(entry.get("failure_pattern", "")), MAX_FAILURE_PATTERN, "failure_pattern"
        )
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
        p.text = _cap(str(entry.get("text", "")), MAX_FACT_TEXT, "text")
        raw_tags = entry.get("tags", [])
        if not isinstance(raw_tags, list):
            raise MnemeError("tags must be a list")
        p.tags = [str(t) for t in raw_tags]
        if len(p.tags) > MAX_TAGS:
            raise MnemeError(f"tags exceeds {MAX_TAGS} entries ({len(p.tags)})")
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
    except RecursionError:
        # Deep nesting (`[[[[...]]]]`) blows the C scanner's stack and raises
        # RecursionError, which is *not* a JSONDecodeError. This is the LLM-output trust
        # boundary: hostile input must come back as a MnemeError, never a traceback.
        raise MnemeError("proposals are nested too deeply to parse") from None
    except ValueError as e:
        # JSONDecodeError is a ValueError; catching the base class also covers the
        # other ValueErrors json can raise (e.g. NaN/Infinity handling).
        raise MnemeError(f"proposals are not valid JSON: {e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        raise MnemeError("proposals document must be an object with a 'proposals' list")
    if len(data["proposals"]) > MAX_PROPOSALS:
        raise MnemeError(
            f"proposals document has {len(data['proposals'])} entries; max {MAX_PROPOSALS}"
        )
    valid: list[Proposal] = []
    errors: list[str] = []
    for i, entry in enumerate(data["proposals"]):
        try:
            valid.append(_validate(entry))
        except MnemeError as e:
            errors.append(f"proposal {i}: {e}")
        except RecursionError:
            # A value nested just under the parser's limit survives json.loads but blows
            # the stack when _validate stringifies it. One bad entry is a rejection, not
            # a crash of the whole ingest.
            errors.append(f"proposal {i}: value is nested too deeply to validate")
    return valid, errors
