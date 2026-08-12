"""Registered knowledge plugins — flat-file source of truth (spec §4.2)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths
from .errors import MnemeError
from .units import KEBAB_RE

SENSITIVITIES = frozenset({"public", "internal", "restricted"})


@dataclass
class Plugin:
    name: str
    repo: str
    path: str
    sensitivity: str = "internal"
    exclusions: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not KEBAB_RE.match(self.name):
            raise MnemeError(f"plugin name must be kebab-case: {self.name!r}")
        if not self.repo:
            raise MnemeError("plugin repo must not be empty")
        if self.sensitivity not in SENSITIVITIES:
            raise MnemeError(
                f"sensitivity must be one of {sorted(SENSITIVITIES)}: {self.sensitivity!r}"
            )


def load_registry(home: Path) -> list[Plugin]:
    p = paths.registry_path(home)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    known = {f.name for f in Plugin.__dataclass_fields__.values()}
    return [
        Plugin(**{k: v for k, v in entry.items() if k in known})
        for entry in data.get("plugins", [])
    ]


def save_registry(home: Path, plugins: list[Plugin]) -> None:
    for pl in plugins:
        pl.validate()
    paths.ensure_layout(home)
    payload = {"version": 1, "plugins": [asdict(pl) for pl in plugins]}
    paths.registry_path(home).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def add_plugin(home: Path, plugin: Plugin) -> None:
    plugins = load_registry(home)
    if any(p.name == plugin.name for p in plugins):
        raise MnemeError(f"plugin already registered: {plugin.name}")
    plugins.append(plugin)
    save_registry(home, plugins)


def remove_plugin(home: Path, name: str) -> None:
    plugins = load_registry(home)
    kept = [p for p in plugins if p.name != name]
    if len(kept) == len(plugins):
        raise MnemeError(f"plugin not registered: {name}")
    save_registry(home, kept)


def get_plugin(home: Path, name: str) -> Plugin | None:
    for p in load_registry(home):
        if p.name == name:
            return p
    return None
