# Plan 16 — finish the backlog

Three findings from the Plan 14 adversarial review, all reproduced, none yet fixed.

---

## Task 1: the boundary check has never fired in the shipped pipeline

**Files:** `core/mneme_core/flags.py`, `core/mneme_core/cli.py`;
`tests/core/test_flag_origin.py` (new)

`cli._distill_ingest` computes the boundary warning from
`scope_by_name.get(args.source_plugin)`. `bin/mneme-distill-pipeline` passes only
`--source "session:<transcript>"`, never `--source-plugin`, so `source_scope` is always
`None`: every candidate is staged with `boundary_warning=""` and `source_sensitivity=""`,
and ingest reports `boundary-warnings 0` whatever it staged. The `[boundary]` flag — the
thing that stops restricted knowledge drifting toward a less-restricted repo — has never
fired outside a hand-run `distill ingest`. `docs/getting-started.md` already admits it.

**The fix is not in the shell script.** The pipeline already hands ingest the bundle as
`--flags-snapshot`, and that bundle already carries the flag records. So if a flag records
where it was captured, ingest can derive the source with **no change to the pipeline at
all** — one less moving part, and it works for anyone driving `distill ingest` by hand too.

- `flags.add_flag` records `cwd` (absolute, resolved). Backwards compatible; absent on
  every flag written before this, and read as unknown.
- `cli._distill_ingest`, when `--source-plugin` is not given, resolves each snapshot flag's
  `cwd` through `routing.plugin_for_path` and takes the **most restricted** scope among
  them. Most-restricted, not first: a session that touched two repos must be judged by the
  tighter one, or mixing a restricted repo into the session launders everything in it.
- A flag captured outside every registered repo contributes nothing, and if no flag
  resolves, the source stays unknown — which is the honest answer and exactly what
  `staging.route`'s "unverified" note already reports.

- [x] **Steps:** failing tests (origin recorded, absent on old flags, most-restricted wins,
  none-resolve stays unknown, explicit `--source-plugin` still wins) → implement → verify
  the real pipeline path end to end.

## Task 2: nothing in `core/` takes a lock

**Files:** `core/mneme_core/paths.py`, `core/mneme_core/staging.py`,
`core/mneme_core/indexing.py`; `tests/core/test_locking.py` (new)

Two concurrent `mneme share route` calls on one candidate produce **two** candidates, both
exiting 0. `grep -rn 'flock\|fcntl\|\.lock' core/` returns nothing, so this is not specific
to routing: a route racing the distiller's `stage`, or the Stop and PreCompact hooks both
firing, have the same shape.

`paths.locked(home, name)` — a context manager over `fcntl.flock` on a lock file under
`MNEME_HOME`. Advisory, process-scoped, released by the OS if the holder dies, so it cannot
wedge a later run the way a stale lock file would. `flock` is POSIX; on a platform without
it the lock degrades to a no-op rather than an import error, because refusing to run is
worse than the race it prevents.

Held by the mutating paths that read-then-write: `staging.route`, `staging.decline`,
`flags.consume_flags`, and `indexing.rebuild`. NOT by `search` — it is read-only and taking
a lock there would put a write-shaped wait on the hot path.

- [x] **Steps:** failing test (two routes on one candidate yield one) → implement → verify
  a dead holder's lock is reclaimed → confirm no read path takes it.

## Task 3: a routing correction is invisible to the distiller

**Files:** `core/mneme_core/staging.py`, `core/mneme_core/cli.py`;
`tests/core/test_share_route.py`

Re-minting the id stops the distiller staging a twin of the CORRECTED candidate. It does
nothing about the mis-route: the distiller's guess has not changed, so the next ingest
stages the same sentence for the original target again and the gate shows it under two
targets. Approving both puts one sentence in two repos.

The declined ledger is the wrong home — a route is not a rejection of the knowledge, and
reusing it would block routing back later. `staging.record_route` appends to
`routed.jsonl`: `{from_target, to_target, hash, text_hash, ts}`, and the stage path skips a
proposal whose body was already routed AWAY from the target it is being proposed for.

Routing it back clears the record, because the human has changed their mind and the ledger
must not outlive the decision it describes.

- [x] **Steps:** failing test (route, re-ingest the same proposal, it is not re-staged) →
  implement → verify routing back re-enables it.

## Verification

1. A flag captured inside a restricted repo produces a boundary warning at the gate,
   through the real pipeline path, with no `--source-plugin` anywhere.
2. Two concurrent routes on one candidate leave exactly one.
3. A corrected route survives the next distill run.
4. `search` takes no lock.
