"""Candidate staging area and declined ledger (spec §7.2–7.3)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
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
    # The sensitivity of the context this knowledge was captured IN. Persisted because
    # `boundary_warning` is a RENDERED STRING: re-routing has to recompute the check
    # against a new destination, and the only input it cannot recover afterwards is
    # where the knowledge came from. Absent on anything staged before this existed, and
    # every reader treats "" as unknown rather than as "public".
    source_sensitivity: str = ""
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
        "source-sensitivity": cand.source_sensitivity,
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
        source_sensitivity=str(meta.get("source-sensitivity", "")),
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


def route(home: Path, cand_id: str, target: str, *, allow_boundary: bool = False) -> Candidate:
    """Point a staged candidate at a different plugin, and return it as it now stands.

    The alternative this replaces was decline-and-reflag, which is not equivalent: a
    decline is a permanent human verdict, and for a candidate with NO destination
    `_applies_to` deliberately records it GLOBALLY — so the only sanctioned way to fix a
    routing mistake silenced the knowledge in every repo forever.

    The id is RE-MINTED, because `candidate_id` hashes the target with the body. Keeping
    the old one would leave an id that no longer derives from its inputs, and the next
    distiller run would stage the same knowledge again under the correct id — the gate
    would then show the same sentence twice and a human would have to notice.

    Everything refused below is refused before anything is written, so a rejected move
    leaves the queue exactly as it found it.
    """
    from . import registry, routing

    path = _find(home, cand_id)
    if path is None:
        raise MnemeError(f"no staged candidate {cand_id} — check `mneme share list --all`")
    cand = _from_text(path.read_text(encoding="utf-8"))

    if not target:
        raise MnemeError(
            f"no target given — pass a registered plugin, or '{UNASSIGNED}' to un-route"
        )
    if target == cand.target:
        raise MnemeError(f"{cand_id} is already routed to {target}")
    # An `update` names a unit by id in the repo it is LEAVING. Carried across, the harvest
    # either aborts the whole batch on a missing target file — taking every other approved
    # candidate with it — or, when a topic key collides in the destination, silently
    # rewrites an unrelated bullet. Parking at `unassigned` is safe because it takes the
    # candidate out of every repo, so the stale reference reaches nothing.
    if cand.edit == "update" and target != UNASSIGNED:
        raise MnemeError(
            f"{cand_id} is an update to {cand.target_unit or 'a unit'} in {cand.target} —"
            " that reference does not survive a move to another repo. Decline it and"
            f" re-flag against {target}, or park it with --target {UNASSIGNED}."
        )
    scope = None
    if target != UNASSIGNED:
        scope = next((s for s in routing.scopes(home) if s.name == target), None)
        if scope is None:
            known = ", ".join(sorted(p.name for p in registry.load_registry(home))) or "none"
            raise MnemeError(
                f"{target} is not registered — registered plugins: {known}."
                f" Use '{UNASSIGNED}' to leave a candidate unrouted."
            )

    # "Declined stays declined" (spec §7.3) scoped to the DESTINATION: a body a human has
    # already rejected for that repo must not re-enter it by being pointed there.
    if target != UNASSIGNED and is_declined_in(declined_index(home, target), cand.body):
        raise MnemeError(
            f"this knowledge was already declined for {target} — routing it there would"
            " undo that decision. Re-flag it if the verdict has changed."
        )

    new_id = candidate_id(cand.type, target, cand.body)
    if new_id != cand_id and _find(home, new_id) is not None:
        raise MnemeError(
            f"the same knowledge is already staged for {target} ({new_id}) — this is a"
            " duplicate, not a move. Decline one of the two."
        )

    warning, crossing, source = _boundary_for_move(home, cand, scope)
    if crossing and not allow_boundary:
        raise MnemeError(
            f"routing {cand_id} to {target} crosses a sensitivity boundary: {warning}."
            " Re-run with --allow-boundary if that is deliberate."
        )

    # `source_sensitivity` is persisted, and that is the whole defence. Computing it and
    # discarding it let `restricted -> unassigned -> public` complete with no refusal, one
    # command after the direct move had been refused: the first hop erased the only
    # evidence of where the knowledge came from. A similarity hint is dropped on a
    # cross-repo move because it names a unit in a repo this candidate no longer targets.
    moved = replace(
        cand, id=new_id, target=target, boundary_warning=warning,
        source_sensitivity=source or cand.source_sensitivity,
        similar_to="" if target != cand.target else cand.similar_to,
    )
    write_candidate(home, moved)
    if path.exists() and path != _find(home, new_id):
        path.unlink()
    return moved


def _boundary_for_move(home: Path, cand: Candidate, scope) -> tuple[str, bool, str]:
    """(what to record, is this a CROSSING, the source sensitivity to carry forward).

    The third value is the one that matters most. An earlier version computed the source,
    used it once, and threw it away — so parking a candidate at `unassigned` reset it to
    unknown and the next hop into a public repo passed. `restricted -> unassigned ->
    public` completed with no flag, one command after the direct move was refused.

    Resolution order, and each rung is there for a reason:

    1. The sensitivity recorded when the candidate was staged — the real answer.
    2. Otherwise, a boundary warning already ON the candidate means a crossing was found
       before and never resolved. That outranks the current target, because a legacy
       candidate sitting in a public repo with a "source context is restricted" warning
       would otherwise resolve to `public` and lose the flag entirely.
    3. Otherwise, the repo it is currently pointed at: the knowledge was judged fit for
       that repo, which is the conservative reading.
    4. Otherwise nothing is known, and the move says so rather than implying it was
       checked.

    A crossing is a computed fact and needs a human's consent. "Unverified" is the absence
    of a computation: recorded so the gate shows it, but not a refusal — demanding the flag
    for every unrouted candidate, the population this command exists for, is how a flag
    stops being read.
    """
    from . import routing

    source = cand.source_sensitivity
    unresolved = not source and bool(cand.boundary_warning)
    if not source and not unresolved and cand.target != UNASSIGNED:
        current = next((s for s in routing.scopes(home) if s.name == cand.target), None)
        source = current.sensitivity if current is not None else ""

    if scope is None:
        # Un-routing moves knowledge nowhere, so it crosses nothing — but it must NOT be a
        # laundering step: whatever was known travels with the candidate.
        return cand.boundary_warning, False, source

    if unresolved:
        return cand.boundary_warning, True, source
    if not source:
        return (
            f"boundary unverified — nothing is recorded about where this knowledge came"
            f" from, so its fitness for {scope.name} ({scope.sensitivity}) was not checked",
            False,
            "",
        )
    warning = routing.boundary_warning(source, scope)
    return warning, bool(warning), source


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
