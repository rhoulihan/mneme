"""Two processes mutating staging at once — nothing in `core/` took a lock.

An adversarial review reproduced it: two concurrent `mneme share route` calls on one
candidate produce TWO candidates, both exiting 0. Read, write-new, unlink-old, with nothing
serialising it. `grep -rn 'flock|fcntl|\\.lock' core/` returned nothing, so this was never
specific to routing — a route racing the distiller's `stage`, or the Stop and PreCompact
hooks both firing at the end of one session, have the same shape.

The lock is advisory and process-scoped (`fcntl.flock`), which matters: the OS releases it
when the holder dies, so a SIGKILL cannot wedge every later run the way a stale lock FILE
would. Read paths deliberately do not take it — `search` is read-only, and a write-shaped
wait on the agent's hot path is a worse bug than the race it would prevent.
"""
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

from mneme_core import flags, paths, registry, staging
from mneme_core.registry import Plugin
from mneme_core.staging import Candidate, candidate_id

BODY = "- [gotcha] The webhook replays for 72 hours #x (verified: 2026-08-24)\n"


def home_with(tmp_path, *plugins):
    home = tmp_path / "home"
    for name in plugins:
        registry.add_plugin(home, Plugin(name=name, repo="r", path=str(tmp_path / name)))
    return home


def stage(home, target):
    cand = Candidate(
        id=candidate_id("fact", target, BODY), type="fact", edit="new",
        target=target, body=BODY, topic="webhooks",
    )
    staging.write_candidate(home, cand)
    return cand


# --- the primitive -----------------------------------------------------------


def test_the_lock_serialises_two_holders(tmp_path):
    home = tmp_path / "home"
    paths.ensure_layout(home)
    order = []
    with paths.locked(home, "staging"):
        order.append("outer-in")
        # A second acquisition from another PROCESS must wait; from this one we only prove
        # the lock file exists and the manager is re-entrant-safe to release.
        assert paths.lock_path(home, "staging").exists()
        order.append("outer-out")
    assert order == ["outer-in", "outer-out"]
    with paths.locked(home, "staging"):
        pass  # releasable and re-acquirable


def _hold_then_release(home_str, seconds, started, done):
    from mneme_core import paths as p

    home = Path(home_str)
    with p.locked(home, "staging"):
        started.set()
        time.sleep(seconds)
    done.set()


def test_a_second_process_waits_for_the_first(tmp_path):
    home = tmp_path / "home"
    paths.ensure_layout(home)
    ctx = multiprocessing.get_context("fork")
    started, done = ctx.Event(), ctx.Event()
    proc = ctx.Process(target=_hold_then_release, args=(str(home), 0.6, started, done))
    proc.start()
    try:
        assert started.wait(5), "the holder never started"
        t0 = time.perf_counter()
        with paths.locked(home, "staging"):
            waited = time.perf_counter() - t0
    finally:
        proc.join(10)
    assert waited > 0.3, f"acquired in {waited:.2f}s — the lock did not block"


def _die_holding(home_str, started):
    from mneme_core import paths as p

    home = Path(home_str)
    cm = p.locked(home, "staging")
    cm.__enter__()
    started.set()
    os._exit(9)  # SIGKILL-shaped: no cleanup runs


def test_a_dead_holders_lock_is_reclaimed(tmp_path):
    """The reason this is `flock` and not a lock FILE: a killed process cannot wedge every
    later run, because the OS drops the lock when the fd closes."""
    home = tmp_path / "home"
    paths.ensure_layout(home)
    ctx = multiprocessing.get_context("fork")
    started = ctx.Event()
    proc = ctx.Process(target=_die_holding, args=(str(home), started))
    proc.start()
    assert started.wait(5)
    proc.join(10)

    t0 = time.perf_counter()
    with paths.locked(home, "staging"):
        pass
    assert time.perf_counter() - t0 < 2, "a dead holder's lock was not reclaimed"


# --- the paths that take it ---------------------------------------------------


def _route(home_str, target, cand_id, results, idx):
    from mneme_core import staging as st

    home = Path(home_str)
    try:
        st.route(home, cand_id, target)
        results[idx] = "ok"
    except Exception as e:
        results[idx] = type(e).__name__


def test_two_concurrent_routes_leave_exactly_one_candidate(tmp_path):
    """The reproduced failure: both exited 0 and the queue held two candidates."""
    home = home_with(tmp_path, "team-kb", "ops-kb", "eng-kb")
    cand = stage(home, "team-kb")

    ctx = multiprocessing.get_context("fork")
    results = ctx.Array("u", 40)
    procs = [
        ctx.Process(target=_route, args=(str(home), t, cand.id, results, i))
        for i, t in enumerate(("ops-kb", "eng-kb"))
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(20)

    assert len(staging.load_candidates(home, include_quarantined=True)) == 1


def test_a_decline_races_a_route_without_duplicating(tmp_path):
    home = home_with(tmp_path, "team-kb", "ops-kb")
    cand = stage(home, "team-kb")

    ctx = multiprocessing.get_context("fork")
    results = ctx.Array("u", 40)

    def _decline(home_str, cid, res, i):
        from mneme_core import staging as st

        try:
            c = st.load_candidates(Path(home_str))[0]
            st.decline(Path(home_str), c, "no")
            res[i] = "ok"
        except Exception as e:
            res[i] = type(e).__name__

    procs = [
        ctx.Process(target=_route, args=(str(home), "ops-kb", cand.id, results, 0)),
        ctx.Process(target=_decline, args=(str(home), cand.id, results, 1)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(20)

    assert len(staging.load_candidates(home, include_quarantined=True)) <= 1


# --- and the paths that must NOT take it -------------------------------------


def test_the_read_path_does_not_take_the_lock(tmp_path):
    """`search` is read-only. A write-shaped wait on the agent's hot path would be a worse
    bug than the race it prevents, so a held lock must not block a read."""
    from mneme_core import indexing, scaffold

    home = tmp_path / "home"
    scaffold.create(home, "team-kb", owner="demo", directory=tmp_path / "team-kb")
    indexing.rebuild(home)

    with paths.locked(home, "staging"):
        t0 = time.perf_counter()
        indexing.stale(home)  # the read `search` performs
        staging.load_candidates(home)
        assert time.perf_counter() - t0 < 2, "a read waited on the write lock"


def test_the_lock_lives_under_mneme_home_not_a_repo(tmp_path):
    """It guards MNEME_HOME state — staging, flags, the index — never a knowledge repo,
    which is git's to arbitrate."""
    home = tmp_path / "home"
    paths.ensure_layout(home)
    with paths.locked(home, "staging"):
        p = paths.lock_path(home, "staging")
    assert home in p.parents
