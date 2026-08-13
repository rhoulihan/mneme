"""Schema lint for knowledge units — machines settle format (spec §5, §7.4)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import units
from .errors import MnemeError

MAX_DESCRIPTION = 1024


@dataclass
class LintIssue:
    path: str
    line: int
    code: str
    severity: str
    message: str


def lint_skill(skill_dir: Path) -> list[LintIssue]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [LintIssue(str(skill_md), 0, "MN001", "error", "SKILL.md not found")]
    text = _text(skill_md)
    if text is None:
        return [LintIssue(str(skill_md), 0, "MN010", "error", "not valid UTF-8")]
    try:
        meta, _body = units.parse_frontmatter(text)
    except MnemeError as e:
        return [LintIssue(str(skill_md), 0, "MN010", "error", str(e))]
    if not meta:
        return [LintIssue(str(skill_md), 0, "MN001", "error", "missing frontmatter")]
    issues: list[LintIssue] = []
    name = str(meta.get("name", ""))
    if not name or not units.KEBAB_RE.match(name):
        issues.append(
            LintIssue(str(skill_md), 0, "MN002", "error", f"name missing or not kebab-case: {name!r}")
        )
    elif name != skill_dir.name:
        issues.append(
            LintIssue(
                str(skill_md), 0, "MN003", "error",
                f"name {name!r} does not match directory {skill_dir.name!r}",
            )
        )
    description = str(meta.get("description", ""))
    if not description:
        issues.append(LintIssue(str(skill_md), 0, "MN004", "error", "description missing"))
    elif len(description) > MAX_DESCRIPTION:
        issues.append(
            LintIssue(
                str(skill_md), 0, "MN005", "error",
                f"description exceeds {MAX_DESCRIPTION} chars ({len(description)})",
            )
        )
    return issues


def _text(path: Path) -> str | None:
    """The file's text, or None when it is not UTF-8 — never an escaping exception.

    `utf-8-sig` because every other reader of these files uses it (`build._read_unit_text`,
    `classify._fact_entries`, `layout`): under plain `utf-8` a byte-order mark stays in the
    text, `parse_frontmatter` then does not recognise the opening `---`, and lint reports
    MN009 "missing topic" for a file whose topic every other reader can read.

    None rather than a raise because lint is a REPORTER: a file it cannot decode is a
    finding, not a crash. `classify._finalize` calls `lint_repo` inside the try whose
    `except` runs `harvest._abort`, so one undecodable byte anywhere in the repo used to
    hard-reset the pass being recorded — and `layout` routes such a file into the
    canonical directory precisely because it believed lint tolerated it.
    """
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def lint_fact_file(path: Path) -> list[LintIssue]:
    text = _text(path)
    if text is None:
        return [LintIssue(str(path), 0, "MN010", "error", "not valid UTF-8")]
    try:
        meta, body = units.parse_frontmatter(text)
    except MnemeError as e:
        return [LintIssue(str(path), 0, "MN010", "error", str(e))]
    issues: list[LintIssue] = []
    if "topic" not in meta:
        issues.append(LintIssue(str(path), 0, "MN009", "error", "missing 'topic' frontmatter"))
    offset = len(text.splitlines()) - len(body.splitlines())
    for n, line in enumerate(body.splitlines(), start=1):
        if not line.startswith("- ["):
            continue
        abs_line = offset + n
        try:
            bullet = units.parse_bullet_line(line, n)
        except MnemeError:
            issues.append(
                LintIssue(str(path), abs_line, "MN006", "error", f"malformed fact bullet: {line!r}")
            )
            continue
        if bullet.category not in units.FACT_CATEGORIES:
            issues.append(
                LintIssue(
                    str(path), abs_line, "MN007", "error",
                    f"unknown fact category: {bullet.category!r}",
                )
            )
        if bullet.verified is None:
            issues.append(
                LintIssue(str(path), abs_line, "MN008", "warn", "bullet missing verified date")
            )
    return issues


def lint_repo(root: Path) -> list[LintIssue]:
    issues: list[LintIssue] = []
    skills_dir = root / "skills"
    if skills_dir.is_dir():
        for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            issues.extend(lint_skill(d))
    # Both layouts, never just one: an unlinted fact file is an unenforced format, and CI
    # would pass over a malformed bullet that is committed and on disk.
    for f in units.fact_files(root):
        issues.extend(lint_fact_file(f))
    return issues


def has_errors(issues: list[LintIssue]) -> bool:
    return any(i.severity == "error" for i in issues)
