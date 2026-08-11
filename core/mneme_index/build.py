"""Index builder — walks skill/fact trees into the units index (spec §6.1, §6.3)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mneme_core import units
from mneme_core.errors import MnemeError


@dataclass
class IndexStats:
    plugin: str
    skills: int = 0
    facts: int = 0
    skipped: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def index_tree(
    conn: sqlite3.Connection,
    plugin: str,
    root: Path,
    *,
    repo: str = "",
    mode: str = "",
    sensitivity: str = "",
) -> IndexStats:
    if not root.is_dir():
        raise MnemeError(f"index root is not a directory: {root}")
    stats = IndexStats(plugin=plugin)
    raw_rows = _skill_rows(plugin, root, stats.skipped) + _fact_rows(plugin, root, stats.skipped)
    seen: set[str] = set()
    rows: list[tuple] = []
    for r in raw_rows:
        if r[1] in seen:
            stats.skipped.append(f"{r[7]}:{r[8]}: duplicate unit id {r[1]}")
            continue
        seen.add(r[1])
        rows.append(r)
    stats.skills = sum(1 for r in rows if r[2] == "skill")
    stats.facts = sum(1 for r in rows if r[2] == "fact")

    conn.execute("DELETE FROM units WHERE plugin = ?", (plugin,))
    conn.execute("DELETE FROM units_fts WHERE plugin = ?", (plugin,))
    conn.executemany(
        "INSERT INTO units (plugin, id, kind, name, description, category, tags,"
        " path, line, verified, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.executemany(
        "INSERT INTO units_fts (plugin, id, name, description, tags) VALUES (?, ?, ?, ?, ?)",
        [(r[0], r[1], r[3], r[4], r[6]) for r in rows],
    )
    conn.execute(
        "INSERT INTO plugins (name, root, repo, mode, sensitivity, built_at)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET root = excluded.root, repo = excluded.repo,"
        " mode = excluded.mode, sensitivity = excluded.sensitivity, built_at = excluded.built_at",
        (plugin, str(root), repo, mode, sensitivity, _now()),
    )
    conn.commit()
    return stats


def _skill_rows(plugin: str, root: Path, skipped: list[str]) -> list[tuple]:
    rows: list[tuple] = []
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        return rows
    for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        skill_md = d / "SKILL.md"
        rel = str(skill_md.relative_to(root))
        if not skill_md.exists():
            skipped.append(f"{rel}: SKILL.md not found")
            continue
        text = skill_md.read_text(encoding="utf-8")
        try:
            meta, _body = units.parse_frontmatter(text)
        except MnemeError as e:
            skipped.append(f"{rel}: {e}")
            continue
        name = str(meta.get("name", ""))
        description = str(meta.get("description", ""))
        if not name or not description:
            skipped.append(f"{rel}: missing name or description")
            continue
        keywords = meta.get("keywords", [])
        tags = " ".join(keywords) if isinstance(keywords, list) else str(keywords)
        md = meta.get("metadata", {})
        verified = str(md.get("mneme-last-verified", "")) if isinstance(md, dict) else ""
        rows.append(
            (
                plugin,
                units.skill_unit_id(name),
                "skill",
                name,
                description,
                "",
                tags,
                rel,
                0,
                verified,
                units.content_hash(text),
            )
        )
    return rows


def _fact_rows(plugin: str, root: Path, skipped: list[str]) -> list[tuple]:
    rows: list[tuple] = []
    facts_dir = root / "facts"
    if not facts_dir.is_dir():
        return rows
    for f in sorted(facts_dir.glob("*.md")):
        rel = str(f.relative_to(root))
        text = f.read_text(encoding="utf-8")
        try:
            meta, body = units.parse_frontmatter(text)
        except MnemeError as e:
            skipped.append(f"{rel}: {e}")
            continue
        topic = str(meta.get("topic", f.stem))
        offset = len(text.splitlines()) - len(body.splitlines())
        for n, line in enumerate(body.splitlines(), start=1):
            if not line.startswith("- ["):
                continue
            abs_line = offset + n
            try:
                bullet = units.parse_bullet_line(line, n)
            except MnemeError:
                skipped.append(f"{rel}:{abs_line}: malformed fact bullet")
                continue
            rows.append(
                (
                    plugin,
                    units.fact_unit_id(f.stem, bullet.text),
                    "fact",
                    topic,
                    bullet.text,
                    bullet.category,
                    " ".join(bullet.tags),
                    rel,
                    abs_line,
                    bullet.verified or "",
                    units.content_hash(line),
                )
            )
    return rows
