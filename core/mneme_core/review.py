"""Open-PR triage: reading facts out of pull-request diffs (spec §7.8).

Everything this module reads is UNTRUSTED contributor text — bullet bodies, file paths,
PR titles. Parsing is therefore total: a malformed hunk, a binary blob, a CRLF diff, or a
path trying to walk out of the repo yields a `skipped` note, never an exception and never
a path a later write could follow. Nothing here writes anything: triage reads the repo's
own facts and shells out to READ-ONLY `gh` commands, and every remote-mutating action
(merge, close, comment) stays a human-approved agent action outside these rails.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import gitops, paths, staging, templates, units
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


def _repo_bullet_hashes(repo: Path) -> set[str]:
    """Semantic hash of every fact bullet the repo already carries, both layouts.

    Date-independent by construction (`units.semantic_hash`): a PR re-proposing a known
    fact with today's `verified:` stamp is the ordinary duplicate, and hashing the raw
    line would miss exactly that case.
    """
    hashes: set[str] = set()
    for f in units.fact_files(repo):
        try:
            _meta, body = units.parse_frontmatter(f.read_text(encoding="utf-8-sig"))
        except (MnemeError, OSError, UnicodeDecodeError):
            continue  # lint owns file health; triage must still report the other PRs
        for line in body.splitlines():
            if line.startswith("- ["):
                hashes.add(units.semantic_hash(line))
    return hashes


def _open_index(home: Path):
    """A read-only index connection, or None — the hint is optional by design."""
    db_file = paths.db_path(home)
    if not db_file.exists():
        return None
    try:
        from mneme_index import db as index_db

        return index_db.open_db_readonly(db_file)
    except MnemeError:
        return None


def _similar_to(conn, plugin: str, text: str) -> str:
    """The index's nearest unit id in THIS plugin, or "" — never a blocker.

    Scoped to the plugin under review because the label answers "may this repo already
    cover it": a near hit in some other registered plugin is not evidence about this PR.
    """
    if conn is None:
        return ""
    try:
        from mneme_index import search as index_search

        hits = index_search.search(conn, text, k=1, plugin=plugin)
    except MnemeError:
        return ""
    return str(hits[0]["id"]) if hits else ""


def _status(duplicate: bool, declined: bool, similar_to: str) -> str:
    # Ordered by how conclusive the evidence is: an exact match against the repo settles
    # the question, a human's decline settles it next, and an index neighbour only raises
    # one. Whatever the label, the verdict is the agent's and the action is the user's.
    if duplicate:
        return "duplicate"
    if declined:
        return "declined"
    if similar_to.startswith("skills/"):
        return "possibly-integrated"
    return "new"


def triage(home: Path, cwd: Path) -> dict:
    """Every open PR of the plugin containing `cwd`, with each addition annotated."""
    from . import classify

    scope, repo = classify.resolve(home, cwd)
    # Seeded with the repo, then grown PR by PR in listing order: that single set is what
    # makes the second PR proposing a fact a duplicate of the first, not of nothing.
    seen = _repo_bullet_hashes(repo)
    conn = _open_index(home)
    prs: list[dict] = []
    try:
        for pr in gitops.list_open_prs(repo):
            try:
                number = int(pr.get("number"))
            except (TypeError, ValueError):
                continue  # nothing to fetch a diff for
            skipped: list[str] = []
            try:
                diff = gitops.pr_diff(repo, number)
            except MnemeError as exc:
                # One unreadable PR must not hide the others: report it and move on.
                diff = ""
                skipped.append(f"PR {number}: diff unavailable ({exc})")
            facts, parse_skipped = parse_added_facts(number, diff)
            skipped.extend(parse_skipped)
            entries: list[dict] = []
            for fact in facts:
                digest = units.semantic_hash(fact.line)
                duplicate = digest in seen
                seen.add(digest)
                declined = staging.is_declined(home, fact.line)
                similar_to = _similar_to(conn, scope.name, fact.text)
                entries.append(
                    asdict(fact)
                    | {
                        "duplicate": duplicate,
                        "declined": declined,
                        "similar_to": similar_to,
                        "status": _status(duplicate, declined, similar_to),
                    }
                )
            prs.append(
                {
                    "number": number,
                    "title": str(pr.get("title", "")),
                    "author": str(pr.get("author", "")),
                    "url": str(pr.get("url", "")),
                    "facts": entries,
                    "skills_added": parse_added_skills(number, diff),
                    "skipped": skipped,
                }
            )
    finally:
        if conn is not None:
            conn.close()
    return {
        "plugin": scope.name,
        "repo": str(repo),
        "prs": prs,
        "instructions": templates.REVIEW_INSTRUCTIONS,
    }
