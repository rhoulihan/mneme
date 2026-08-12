"""Routing support: registered scopes and sensitivity boundaries (spec §4.3)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import registry


@dataclass
class Scope:
    name: str
    sensitivity: str
    mode: str
    path: str
    statement: str


def read_scope_statement(mneme_md: Path) -> str:
    try:
        text = mneme_md.read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    lines = text.splitlines()
    collected: list[str] = []
    in_section = False
    for line in lines:
        if line.strip().lower() == "## scope statement":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            collected.append(line)
    return "\n".join(collected).strip()


def scopes(home: Path) -> list[Scope]:
    out: list[Scope] = []
    for p in sorted(registry.load_registry(home), key=lambda pl: pl.name):
        statement = read_scope_statement(Path(p.path) / "MNEME.md")
        out.append(
            Scope(
                name=p.name,
                sensitivity=p.sensitivity,
                mode="",
                path=p.path,
                statement=statement,
            )
        )
    return out


SENSITIVITY_RANK = {"public": 0, "internal": 1, "restricted": 2}


def _rank(sensitivity: str) -> int:
    return SENSITIVITY_RANK.get(sensitivity, SENSITIVITY_RANK["internal"])


def boundary_warning(source_sensitivity: str, target: Scope) -> str:
    if _rank(target.sensitivity) < _rank(source_sensitivity):
        return (
            f"target '{target.name}' is {target.sensitivity} but the source context"
            f" is {source_sensitivity}"
        )
    return ""


def plugin_for_path(home: Path, cwd: Path) -> Scope | None:
    best: Scope | None = None
    best_depth = -1
    cwd = cwd.resolve()
    for s in scopes(home):
        root = Path(s.path).resolve()
        if root == cwd or root in cwd.parents:
            depth = len(root.parts)
            if depth > best_depth:
                best, best_depth = s, depth
    return best


def find_knowledge_repo(cwd: Path, max_depth: int = 20) -> Path | None:
    try:
        current = cwd.resolve()
        if not current.exists():
            return None
        for _ in range(max_depth):
            if (current / "MNEME.md").is_file():
                return current
            if current.parent == current:
                return None
            current = current.parent
    except OSError:
        return None
    return None
