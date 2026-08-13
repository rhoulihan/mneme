"""Candidate staging area and declined ledger (spec §7.2–7.3)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import paths, units
from .errors import MnemeError

TYPES = frozenset({"skill", "fact"})
EDITS = frozenset({"new", "update"})
UNASSIGNED = "unassigned"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Candidate:
    id: str
    type: str
    edit: str
    target: str
    body: str
    confidence: float = 0.5
    rationale: str = ""
    target_unit: str = ""
    topic: str = ""
    similar_to: str = ""
    boundary_warning: str = ""
    provenance: dict = field(default_factory=dict)
    status: str = "staged"

    def validate(self) -> None:
        if self.type not in TYPES:
            raise MnemeError(f"candidate type must be one of {sorted(TYPES)}: {self.type!r}")
        if self.edit not in EDITS:
            raise MnemeError(f"candidate edit must be one of {sorted(EDITS)}: {self.edit!r}")
        if self.edit == "update" and not self.target_unit:
            raise MnemeError("update candidates must set target_unit")
        if not self.body.strip():
            raise MnemeError("candidate body must not be empty")
        if self.status not in ("staged", "quarantined"):
            raise MnemeError(f"unknown candidate status: {self.status!r}")


def candidate_id(type_: str, target: str, body: str) -> str:
    # semantic_hash, not content_hash: the body carries a freshly stamped capture date,
    # so hashing it raw would mint a new id for identical knowledge every day and stage
    # a duplicate on each run.
    digest = units.semantic_hash(target + "\n" + body)
    return f"{type_}-{digest}"


def _to_text(cand: Candidate) -> str:
    meta = {
        "id": cand.id,
        "type": cand.type,
        "edit": cand.edit,
        "target": cand.target,
        "confidence": str(cand.confidence),
        "rationale": cand.rationale,
        "target-unit": cand.target_unit,
        "topic": cand.topic,
        "similar-to": cand.similar_to,
        "boundary-warning": cand.boundary_warning,
        "status": cand.status,
        "provenance": {k: str(v) for k, v in cand.provenance.items()},
    }
    return units.serialize_frontmatter(meta, cand.body)


def _from_text(text: str) -> Candidate:
    meta, body = units.parse_frontmatter(text)
    return Candidate(
        id=str(meta.get("id", "")),
        type=str(meta.get("type", "")),
        edit=str(meta.get("edit", "")),
        target=str(meta.get("target", UNASSIGNED)),
        body=body,
        confidence=float(meta.get("confidence", "0.5")),
        rationale=str(meta.get("rationale", "")),
        target_unit=str(meta.get("target-unit", "")),
        topic=str(meta.get("topic", "")),
        similar_to=str(meta.get("similar-to", "")),
        boundary_warning=str(meta.get("boundary-warning", "")),
        provenance=dict(meta.get("provenance", {})),
        status=str(meta.get("status", "staged")),
    )


def _find(home: Path, cand_id: str) -> Path | None:
    for d in (paths.staging_dir(home), paths.quarantine_dir(home)):
        p = d / f"{cand_id}.md"
        if p.exists():
            return p
    return None


def write_candidate(home: Path, cand: Candidate) -> Path:
    cand.validate()
    paths.ensure_layout(home)
    directory = (
        paths.quarantine_dir(home) if cand.status == "quarantined" else paths.staging_dir(home)
    )
    path = directory / f"{cand.id}.md"
    path.write_text(_to_text(cand), encoding="utf-8")
    return path


def load_candidates(home: Path, include_quarantined: bool = False) -> list[Candidate]:
    dirs = [paths.staging_dir(home)]
    if include_quarantined:
        dirs.append(paths.quarantine_dir(home))
    out: list[Candidate] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            try:
                out.append(_from_text(p.read_text(encoding="utf-8")))
            except MnemeError as e:
                raise MnemeError(f"{p}: {e}") from e
    return sorted(out, key=lambda c: c.id)


def remove_candidate(home: Path, cand_id: str) -> None:
    p = _find(home, cand_id)
    if p is None:
        raise MnemeError(f"no staged candidate with id: {cand_id}")
    p.unlink()


def quarantine(home: Path, cand_id: str) -> Path:
    p = _find(home, cand_id)
    if p is None:
        raise MnemeError(f"no staged candidate with id: {cand_id}")
    cand = _from_text(p.read_text(encoding="utf-8"))
    cand.status = "quarantined"
    p.unlink()
    return write_candidate(home, cand)


def _read_declined(home: Path) -> list[dict]:
    p = paths.declined_path(home)
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def decline(home: Path, cand: Candidate, reason: str) -> None:
    paths.ensure_layout(home)
    record = {
        "id": cand.id,
        "hash": units.semantic_hash(cand.body),
        "target": cand.target,
        "reason": reason,
        "ts": _now(),
    }
    # A fact also gets the stronger key: `hash` covers the rendered line, so re-proposing
    # a declined bullet under a different `#tag` or `[category]` used to read as brand new
    # knowledge. `text_hash` is the sentence itself, which is what the human rejected.
    text_hash = units.fact_text_hash(cand.body)
    if text_hash is not None:
        record["text_hash"] = text_hash
    with paths.declined_path(home).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    existing = _find(home, cand.id)
    if existing is not None:
        existing.unlink()


def _applies_to(rec: dict, plugin: str | None) -> bool:
    """Does this ledger line answer for `plugin` — or for every plugin?

    A decline is a human's verdict on a candidate that was headed somewhere: "no, not in
    THIS collection". Reading it as a verdict on the sentence everywhere let one repo's
    curation silence the same knowledge for a repo whose maintainers never saw it. Two
    lines stay global on purpose: one written before the field existed, and one for a
    candidate that had no destination — neither ever named a repo to scope to, and
    guessing a scope for them would resurrect knowledge a human has already rejected.
    """
    if plugin is None:
        return True
    target = rec.get("target")
    if not isinstance(target, str) or target in ("", UNASSIGNED):
        return True
    return target == plugin


def declined_index(home: Path, plugin: str | None = None) -> tuple[set[str], set[str]]:
    """`(line hashes, fact-text hashes)` from the declined ledger, read once.

    Callers that ask about many bodies — triage annotates every addition in every open PR —
    read the ledger once instead of once per question, so one large pull request cannot
    turn the ledger into a per-bullet file read. `plugin` narrows the ledger to the
    declines that answer for that plugin (see `_applies_to`); the default reads all of it,
    which is what a caller with no destination in hand — `mneme stage`, a human asking —
    still wants.
    """
    lines: set[str] = set()
    texts: set[str] = set()
    for rec in _read_declined(home):
        if not _applies_to(rec, plugin):
            continue
        h, t = rec.get("hash"), rec.get("text_hash")
        if isinstance(h, str):
            lines.add(h)
        if isinstance(t, str):
            texts.add(t)
    return lines, texts


def is_declined_in(index: tuple[set[str], set[str]], body: str) -> bool:
    # Compared on the date-independent hash so a decline holds tomorrow too — the spec
    # §7.3 guarantee is "declined stays declined", not "declined until midnight UTC".
    # For facts the ledger also carries the text-only key, so the guarantee survives a
    # retag or a recategorization of the same sentence (ledger entries written before that
    # key existed still match on the line hash alone).
    lines, texts = index
    if units.semantic_hash(body) in lines:
        return True
    text_hash = units.fact_text_hash(body)
    return text_hash is not None and text_hash in texts


def is_declined(home: Path, body: str, plugin: str | None = None) -> bool:
    return is_declined_in(declined_index(home, plugin), body)
