"""Registered knowledge plugins — flat-file source of truth (spec §4.2)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths
from .errors import MnemeError
from .units import KEBAB_RE

SENSITIVITIES = frozenset({"public", "internal", "restricted"})
REGISTRY_VERSION = 1
# The field that carries keys this binary does not know. `load_registry` filtered them out
# and `save_registry` rewrote the file from the filtered objects, so ANY write by an older
# binary silently dropped every unknown key for every plugin -- and it failed OPEN: a
# dropped sensitivity binding is not a refusal, it is a missing check.
_CARRIER = "extra"
# Keys mneme used to write and deliberately stopped writing. An UNKNOWN key is preserved --
# a newer binary's field must survive an older binary's write. A RETIRED key is not
# unknown: it is a decision, and carrying it forward would resurrect what was removed.
# `mode` was the pr|commit split, deleted by user direction because mneme never writes a
# registered repo's main; a resave garbage-collects it. The two rules look alike and are
# opposite, so the distinction is named here rather than inferred at either call site.
_RETIRED = frozenset({"mode"})


@dataclass
class Plugin:
    name: str
    repo: str
    path: str
    sensitivity: str = "internal"
    exclusions: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def validate(self) -> None:
        if not KEBAB_RE.match(self.name):
            raise MnemeError(f"plugin name must be kebab-case: {self.name!r}")
        if not self.repo:
            raise MnemeError("plugin repo must not be empty")
        if self.sensitivity not in SENSITIVITIES:
            raise MnemeError(
                f"sensitivity must be one of {sorted(SENSITIVITIES)}: {self.sensitivity!r}"
            )


def _read_document(home: Path) -> dict:
    """The registry file as a document, or `{}` when there is no file yet.

    Corruption is NOT emptiness. Reading an unparseable or unreadable registry as "no
    plugins" was the same fail-open this module exists to close twice over: an empty
    registry makes `routing.scopes()` empty, so every boundary and sensitivity check is
    silently skipped -- and the next `registry add` rewrites the file from that empty
    list, deleting every real plugin permanently. A missing file is the only emptiness.
    """
    p = paths.registry_path(home)
    if not p.exists():
        return {}
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        raise MnemeError(f"cannot read the registry at {p}: {e}") from None
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise MnemeError(
            f"the registry at {p} is not valid JSON ({e}). It is not being rewritten:"
            " fix or remove the file rather than letting mneme start from an empty one."
        ) from None
    if not isinstance(data, dict):
        raise MnemeError(f"the registry at {p} must be a JSON object")
    version = data.get("version", REGISTRY_VERSION)
    # `isinstance(x, int)` is true for `True`, and JSON `2.0` parses to a float -- both
    # slipped past an int-only guard and were silently rewritten as version 1. Checked on
    # READ so a newer registry is never interpreted with this version's field meanings,
    # not only on write.
    if type(version) is not int:
        raise MnemeError(
            f"the registry at {p} has a non-integer version {version!r}"
        )
    if version > REGISTRY_VERSION:
        raise MnemeError(
            f"the registry at {p} is version {version}; this mneme understands"
            f" {REGISTRY_VERSION}. Upgrade mneme rather than letting it rewrite a file it"
            " does not understand."
        )
    return data


def load_registry(home: Path) -> list[Plugin]:
    data = _read_document(home)
    entries = data.get("plugins", [])
    p = paths.registry_path(home)
    if not isinstance(entries, list):
        raise MnemeError(f"the registry at {p} must hold a 'plugins' list")
    known = {f.name for f in Plugin.__dataclass_fields__.values()} - {_CARRIER}
    out: list[Plugin] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise MnemeError(f"{p}: plugin {i} is not an object")
        try:
            out.append(
                Plugin(
                    **{k: v for k, v in entry.items() if k in known},
                    extra={
                        k: v
                        for k, v in entry.items()
                        if k not in known and k not in _RETIRED
                    },
                )
            )
        except TypeError as e:
            raise MnemeError(f"{p}: plugin {i} is malformed ({e})") from None
    return out


def _as_entry(pl: Plugin) -> dict:
    d = asdict(pl)
    carried = d.pop(_CARRIER, None) or {}
    return {**d, **carried}


def save_registry(home: Path, plugins: list[Plugin]) -> None:
    for pl in plugins:
        pl.validate()
    # Reads the document first, so a corrupt or newer-versioned file refuses the write
    # instead of being replaced by it. Preserving unknown KEYS handles a field this binary
    # has not learned; the version check in `_read_document` handles a shape it cannot.
    document = _read_document(home)
    paths.ensure_layout(home)
    preserved = {k: v for k, v in document.items() if k not in ("version", "plugins")}
    payload = {
        **preserved,
        "version": REGISTRY_VERSION,
        "plugins": [_as_entry(pl) for pl in plugins],
    }
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
