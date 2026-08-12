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
                mode=p.mode,
                path=p.path,
                statement=statement,
            )
        )
    return out
