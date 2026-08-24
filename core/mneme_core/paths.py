"""Filesystem layout for mneme local state (spec §4.1)."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import MnemeError


def mneme_home() -> Path:
    env = os.environ.get("MNEME_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".mneme"


def staging_dir(home: Path) -> Path:
    return home / "staging"


def quarantine_dir(home: Path) -> Path:
    return staging_dir(home) / "quarantine"


def repos_dir(home: Path) -> Path:
    return home / "repos"


def logs_dir(home: Path) -> Path:
    return home / "logs"


def registry_path(home: Path) -> Path:
    return home / "registry.json"


def declined_path(home: Path) -> Path:
    return home / "declined.jsonl"


def submitted_path(home: Path) -> Path:
    return home / "submitted.jsonl"


def detection_declined_path(home: Path) -> Path:
    """Repos the user declined to register — a decline that outlives the session.

    Separate from declined_path (candidate bodies): this ledger keys on repo
    paths, and its only job is to stop the registration nudge coming back.
    """
    return home / "detection-declined.jsonl"


def flags_path(home: Path) -> Path:
    return staging_dir(home) / "flags.jsonl"


def routed_path(home: Path) -> Path:
    """Destinations a human has routed knowledge AWAY from.

    Deliberately not the declined ledger. A route is not a rejection of the knowledge —
    reusing `declined.jsonl` would suppress the sentence rather than the destination, and
    would block routing it back later.
    """
    return home / "routed.jsonl"


def db_path(home: Path) -> Path:
    return home / "mneme.db"


def ensure_layout(home: Path) -> Path:
    for d in (home, staging_dir(home), quarantine_dir(home), repos_dir(home), logs_dir(home)):
        d.mkdir(parents=True, exist_ok=True)
    return home


def lock_path(home: Path, name: str) -> Path:
    return home / f".{name}.lock"


@contextmanager
def locked(home: Path, name: str, *, timeout: float = 30.0):
    """Serialise mutations of MNEME_HOME state across processes.

    Nothing in `core/` took a lock, and an adversarial review reproduced what that costs:
    two concurrent `mneme share route` calls on one candidate produced TWO candidates, both
    exiting 0 — read, write-new, unlink-old, with nothing in between. It was never specific
    to routing. A route racing the distiller's `stage`, or the Stop and PreCompact hooks
    both firing at the end of one session, have the same shape.

    `fcntl.flock`, not a lock FILE that has to be cleaned up. The OS drops an advisory lock
    when the holding fd closes, so a process killed mid-write cannot wedge every later run
    — which is exactly how a lock-file scheme fails, and it fails silently and permanently.

    Guards MNEME_HOME state only: staging, flags, the index. Never a knowledge repo, which
    is git's to arbitrate. Read paths deliberately do not take it — `search` is read-only,
    and a write-shaped wait on the agent's hot path is a worse bug than the race it
    prevents.

    Degrades to a no-op where `fcntl` does not exist rather than raising on import: refusing
    to run at all is worse than the race, and the platforms without it are not the ones this
    ships to today.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - POSIX everywhere this ships
        yield
        return

    home.mkdir(parents=True, exist_ok=True)
    path = lock_path(home, name)
    deadline = time.monotonic() + timeout
    with path.open("a+b") as fh:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise MnemeError(
                        f"timed out after {timeout:g}s waiting for the {name} lock"
                        f" ({path}) — another mneme process is holding it"
                    ) from None
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
