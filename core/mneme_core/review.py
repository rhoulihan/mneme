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
_HUNK_RE = re.compile(r"^@@ -\d+(?:,(?P<old>\d+))? \+\d+(?:,(?P<new>\d+))? @@")

_SKIP_REASON_CAP = 200

# A fact bullet is one sentence of knowledge. Anything past this is not a bullet a human
# wrote for a human, and parsing it is unbounded work on input a contributor chooses — so
# it is reported as skipped and never parsed. Real bullets in this repo run ~100 chars.
_MAX_BULLET_LINE = 2000

# The `similar_to` hint is a nicety; the query it builds is one OR-term per word. Capping
# the text keeps one PR from turning an optional label into a minutes-long scan.
_MAX_SIMILAR_QUERY = 500


@dataclass
class _FileDiff:
    """One file's section of a unified diff: its path and the lines it adds/removes."""

    path: str | None
    new_file: bool
    added: list[tuple[int, str]] = field(default_factory=list)
    removed: list[tuple[int, str]] = field(default_factory=list)


def _walk_diff(diff: str) -> list[_FileDiff]:
    """Split a unified diff into per-file sections, tracking hunks properly.

    Hunk tracking is the whole security story here. A diff's CONTENT lines carry a one-char
    prefix, so an added line whose content is `++ b/<path>` renders as `+++ b/<path>` — and
    a line-by-line scan that treats any `+++ ` as a file header lets a pull request that
    touches nothing but `docs/evil.md` attribute fabricated fact bullets to any path it
    names. Inside a hunk (`@@ -a,b +c,d @@`, counted down line by line) NOTHING is a header;
    outside one, a `+++ ` line is a header only where a header may legally appear — right
    after `diff --git ` or `--- `. Nothing here raises: a hunk whose declared counts run out
    keeps consuming content lines (hand-written fixtures undercount) but can no longer name
    a file, because only `diff --git ` and `--- ` re-open the header position.
    """
    files: list[_FileDiff] = []
    current: _FileDiff | None = None
    new_file = False
    header_ready = True
    in_hunk = False
    old_left = new_left = 0
    for n, raw in enumerate(diff.splitlines(), start=1):
        line = raw.rstrip("\r")
        if in_hunk:
            marker = line[:1]
            counted = old_left > 0 or new_left > 0
            # Past the declared counts, a line that looks like the start of the next file
            # or hunk ends this one. While the counts still hold, the hunk owns every line:
            # a `+++ `/`--- ` there is contributor CONTENT, not a header.
            if not counted and (
                line.startswith("diff --git ")
                or line.startswith("--- ")
                or line.startswith("+++ ")
                or _HUNK_RE.match(line)
            ):
                in_hunk = False
            elif marker == "+":
                new_left -= 1
                if current is not None:
                    current.added.append((n, line[1:]))
            elif marker == "-":
                old_left -= 1
                if current is not None:
                    current.removed.append((n, line[1:]))
            elif marker == " " or line == "":
                old_left -= 1
                new_left -= 1
            elif marker == "\\":
                pass  # "\ No newline at end of file" — not a line of either side
            else:
                in_hunk = False  # the hunk ended early; re-read this line as a header
            if in_hunk:
                continue
        if line.startswith("diff --git "):
            current, new_file, header_ready = None, False, True
            continue
        hunk = _HUNK_RE.match(line)
        if hunk:
            old_left = int(hunk.group("old") or 1)
            new_left = int(hunk.group("new") or 1)
            in_hunk = old_left > 0 or new_left > 0
            header_ready = False
            continue
        if line.startswith("new file mode "):
            new_file = True
            continue
        if line.startswith("--- "):
            new_file = new_file or _header_path(line) is None
            header_ready = True
            continue
        if line.startswith("+++ ") and header_ready:
            current = _FileDiff(path=_header_path(line), new_file=new_file)
            files.append(current)
            new_file, header_ready = False, False
    return files


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


def _bullet_facts(
    pr_number: int, side: list[tuple[int, str]], path: str, stem: str
) -> tuple[list[PrFact], list[str]]:
    """Parse one side (added or removed) of a fact file's hunks into bullets."""
    facts: list[PrFact] = []
    skipped: list[str] = []
    for n, content in side:
        bullet_line = content.rstrip()
        if not bullet_line.startswith("- ["):
            continue
        if len(bullet_line) > _MAX_BULLET_LINE:
            skipped.append(
                f"PR {pr_number} {path}: fact bullet at line {n} is"
                f" {len(bullet_line)} characters — over the {_MAX_BULLET_LINE}-character"
                " cap, left for a human to read in the pull request"
            )
            continue
        try:
            bullet = units.parse_bullet_line(bullet_line, n)
        except MnemeError as exc:
            skipped.append(f"PR {pr_number} {path}:{str(exc)[:_SKIP_REASON_CAP]}")
            continue
        facts.append(
            PrFact(
                pr=pr_number,
                file=path,
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


def parse_added_facts(pr_number: int, diff: str) -> tuple[list[PrFact], list[str]]:
    """Fact bullets added by PR `pr_number`, plus a note per unparseable candidate.

    Only lines *added* (`+`) to a fact file count: a diff's context lines are what the repo
    already says, and re-proposing those would make every PR look like a pile of duplicates
    of itself. Removals are read separately (`parse_removed_facts`) — they are the other
    half of what a maintainer has to see.
    """
    return _added_facts(pr_number, _walk_diff(diff))


def _added_facts(
    pr_number: int, sections: list[_FileDiff]
) -> tuple[list[PrFact], list[str]]:
    facts: list[PrFact] = []
    skipped: list[str] = []
    for section in sections:
        stem = _fact_stem(section.path) if section.path else None
        if stem is None:
            continue
        got, notes = _bullet_facts(pr_number, section.added, section.path or "", stem)
        facts.extend(got)
        skipped.extend(notes)
    return facts, skipped


def parse_removed_facts(pr_number: int, diff: str) -> list[PrFact]:
    """Fact bullets a PR DELETES from a fact file.

    Triage used to be addition-only, so a pull request that removed forty bullets produced
    an empty annotation set and read as "clean" — the one path by which knowledge could
    still vanish silently, in the same release that made deletion impossible for mneme's
    own passes. A removal that cannot be parsed is not reported: the bullet was already
    malformed, so lint owns it, and inventing a skip note for it would only add noise.
    """
    return _removed_facts(pr_number, _walk_diff(diff))


def _removed_facts(pr_number: int, sections: list[_FileDiff]) -> list[PrFact]:
    removed: list[PrFact] = []
    for section in sections:
        stem = _fact_stem(section.path) if section.path else None
        if stem is None:
            continue
        got, _notes = _bullet_facts(pr_number, section.removed, section.path or "", stem)
        removed.extend(got)
    return removed


def parse_added_skills(pr_number: int, diff: str) -> list[dict]:
    """New `skills/<name>/SKILL.md` files a PR introduces — surfaced, not judged.

    Restricted to files the diff creates (old side `/dev/null`): a PR that merely
    regenerates `skills/knowledge-index/SKILL.md` is editing the index, not proposing a
    skill, and listing it as an addition would bury the one thing a human must read.
    """
    return _added_skills(pr_number, _walk_diff(diff))


def _added_skills(pr_number: int, sections: list[_FileDiff]) -> list[dict]:
    added: list[dict] = []
    for section in sections:
        if not section.new_file or not section.path:
            continue
        m = _SKILL_PATH_RE.match(section.path)
        if m:
            added.append({"pr": pr_number, "file": section.path, "name": m.group("name")})
    return added


def _repo_bullet_keys(repo: Path) -> tuple[set[str], set[str]]:
    """`(text hashes, unit ids)` for every fact bullet the repo already carries.

    Neither key is the rendered line. `units.semantic_hash` of the line only ignores the
    `verified:` stamp, so a re-proposal that changed one `#tag` or the `[category]` — both
    contributor-controlled — read as brand new knowledge. `units.fact_text_hash` keys on
    the sentence, and the unit id catches the other collision that matters: two bullets
    landing in one topic file under the SAME id, which `harvest.apply_fact` and lint cannot
    tell apart afterwards.
    """
    hashes: set[str] = set()
    ids: set[str] = set()
    for f in units.fact_files(repo):
        try:
            _meta, body = units.parse_frontmatter(f.read_text(encoding="utf-8-sig"))
        except (MnemeError, OSError, UnicodeDecodeError):
            continue  # lint owns file health; triage must still report the other PRs
        for line in body.splitlines():
            if not line.startswith("- ["):
                continue
            digest = units.fact_text_hash(line)
            if digest is None:
                continue
            hashes.add(digest)
            try:
                ids.add(units.fact_unit_id(f.stem, units.parse_bullet_line(line, 1).text))
            except MnemeError:
                continue
    return hashes, ids


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

        hits = index_search.search(conn, text[:_MAX_SIMILAR_QUERY], k=1, plugin=plugin)
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
    seen, seen_ids = _repo_bullet_keys(repo)
    declined_index = staging.declined_index(home)
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
            # One walk per PR: the three views below are derived from it, so a large diff
            # is parsed once rather than once per question asked of it.
            sections = _walk_diff(diff)
            facts, parse_skipped = _added_facts(number, sections)
            skipped.extend(parse_skipped)
            entries: list[dict] = []
            added_keys: set[str] = set()
            for fact in facts:
                digest = units.fact_text_hash(fact.line)
                if digest is not None:
                    added_keys.add(digest)
                duplicate = digest in seen or fact.unit_id in seen_ids
                if digest is not None:
                    seen.add(digest)
                seen_ids.add(fact.unit_id)
                declined = staging.is_declined_in(declined_index, fact.line)
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
            # A deletion is a proposal too — to forget something. `moved` separates a
            # reorganization (the same sentence re-added elsewhere in this PR) from a
            # removal that would take the knowledge out of the repo.
            removed = [
                asdict(fact) | {"moved": units.fact_text_hash(fact.line) in added_keys}
                for fact in _removed_facts(number, sections)
            ]
            prs.append(
                {
                    "number": number,
                    "title": str(pr.get("title", "")),
                    "author": str(pr.get("author", "")),
                    "url": str(pr.get("url", "")),
                    "facts": entries,
                    "removed": removed,
                    "skills_added": _added_skills(number, sections),
                    "skipped": skipped,
                }
            )
    finally:
        if conn is not None:
            conn.close()
    return {
        "plugin": scope.name,
        "repo": str(repo),
        # Where an approved bullet is to be WRITTEN, resolved from this repo's own layout
        # (`units.facts_dir`), plus every topic file that already exists (`units.fact_files`,
        # both layouts). A skill that hardcoded the canonical path sent the agent to create
        # `skills/knowledge-index/facts/deploys.md` in a repo whose `deploys.md` lives in
        # `facts/` — a collision the finalize rail can only refuse. An existing topic is
        # appended to where it already is; only a NEW topic uses `facts_dir`.
        "facts_dir": units.facts_dir(repo).relative_to(repo).as_posix(),
        "fact_files": [f.relative_to(repo).as_posix() for f in units.fact_files(repo)],
        "legacy_layout": (repo / "facts").is_dir(),
        "prs": prs,
        "instructions": templates.REVIEW_INSTRUCTIONS,
    }
