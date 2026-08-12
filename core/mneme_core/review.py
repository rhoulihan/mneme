"""Open-PR triage: reading facts out of pull-request diffs (spec §7.8).

Everything this module reads is UNTRUSTED contributor text — bullet bodies, file paths,
PR titles. Parsing is therefore total: a malformed hunk, a binary blob, a CRLF diff, or a
path trying to walk out of the repo yields a `skipped` note, never an exception and never
a path a later write could follow. Nothing here touches the filesystem or the network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import units
from .errors import MnemeError

# Facts live in exactly two places (`units.facts_dirs`), each a FLAT directory of topic
# files. Matching the whole path against those two shapes — rather than sniffing for a
# "facts" segment anywhere — is what makes traversal (`facts/../../etc/x.md`) and nested
# lookalikes (`vendor/facts/x.md`) non-matches instead of special cases.
_FACT_PATH_RES = (
    re.compile(r"^" + re.escape(units.FACTS_CANONICAL) + r"/(?P<stem>[^/]+)\.md$"),
    re.compile(r"^facts/(?P<stem>[^/]+)\.md$"),
)
_SKILL_PATH_RE = re.compile(r"^skills/(?P<name>[^/]+)/SKILL\.md$")

_SKIP_REASON_CAP = 200


@dataclass
class PrFact:
    """One fact bullet a pull request ADDS, as parsed from its diff."""

    pr: int
    file: str
    stem: str
    line: str
    category: str
    text: str
    tags: list[str] = field(default_factory=list)
    verified: str | None = None
    unit_id: str = ""


def _header_path(line: str) -> str | None:
    """The repo-relative path a `+++ `/`--- ` diff header names, or None for /dev/null."""
    path = line[4:].strip()
    # Plain unified diffs (not `git diff`) append a tab-separated timestamp.
    path = path.split("\t", 1)[0].strip()
    if not path or path == "/dev/null":
        return None
    if path[:2] in ("a/", "b/"):
        path = path[2:]
    # A leading slash or a `..` segment cannot name anything inside the repo; treating
    # such a path as "no current file" keeps hostile diffs from ever naming a target.
    if not path or path.startswith("/") or ".." in path.split("/"):
        return None
    return path


def _fact_stem(path: str) -> str | None:
    for pattern in _FACT_PATH_RES:
        m = pattern.match(path)
        if m:
            return m.group("stem")
    return None


def parse_added_facts(pr_number: int, diff: str) -> tuple[list[PrFact], list[str]]:
    """Fact bullets added by PR `pr_number`, plus a note per unparseable candidate.

    Only lines *added* (`+`) to a fact file count: a diff's context and removed lines are
    what the repo already says, and re-proposing those would make every PR look like a
    pile of duplicates of itself.
    """
    facts: list[PrFact] = []
    skipped: list[str] = []
    path: str | None = None
    stem: str | None = None
    for n, raw in enumerate(diff.splitlines(), start=1):
        line = raw.rstrip("\r")
        if line.startswith("+++ "):
            path = _header_path(line)
            stem = _fact_stem(path) if path else None
            continue
        if line.startswith("--- ") or line.startswith("diff --git "):
            continue
        if stem is None or not line.startswith("+- ["):
            continue
        bullet_line = line[1:].rstrip()
        try:
            bullet = units.parse_bullet_line(bullet_line, n)
        except MnemeError as exc:
            skipped.append(f"PR {pr_number} {path}:{str(exc)[:_SKIP_REASON_CAP]}")
            continue
        facts.append(
            PrFact(
                pr=pr_number,
                file=path or "",
                stem=stem,
                line=bullet_line,
                category=bullet.category,
                text=bullet.text,
                tags=bullet.tags,
                verified=bullet.verified,
                unit_id=units.fact_unit_id(stem, bullet.text),
            )
        )
    return facts, skipped


def parse_added_skills(pr_number: int, diff: str) -> list[dict]:
    """New `skills/<name>/SKILL.md` files a PR introduces — surfaced, not judged.

    Restricted to files the diff creates (old side `/dev/null`): a PR that merely
    regenerates `skills/knowledge-index/SKILL.md` is editing the index, not proposing a
    skill, and listing it as an addition would bury the one thing a human must read.
    """
    added: list[dict] = []
    new_file = False
    for raw in diff.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("diff --git "):
            new_file = False
            continue
        if line.startswith("new file mode "):
            new_file = True
            continue
        if line.startswith("--- "):
            new_file = _header_path(line) is None
            continue
        if not line.startswith("+++ ") or not new_file:
            continue
        path = _header_path(line)
        m = _SKILL_PATH_RE.match(path) if path else None
        if m:
            added.append({"pr": pr_number, "file": path, "name": m.group("name")})
    return added
