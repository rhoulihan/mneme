# Backlog: the boundary check is inert in the shipped pipeline, and staging has no lock

**Status:** raised 2026-08-23 by the Plan 14 adversarial review. Three findings, all
REPRODUCED, none introduced by Plan 14 — but Plan 14 made the first one reachable in a new
way, so it is recorded here rather than left in a transcript.

## 1. `source_sensitivity` is always empty in the shipped pipeline (HIGH)

`cli._distill_ingest` computes the boundary warning from
`scope_by_name.get(args.source_plugin)`. **`bin/mneme-distill-pipeline` never passes
`--source-plugin`** — it passes only `--source "session:<transcript>"`. So in the shipped
path `source_scope` is `None`, every candidate is staged with `boundary_warning=""` and
`source_sensitivity=""`, and `mneme distill ingest` reports `boundary-warnings 0` no matter
what it staged. `docs/getting-started.md` already admits it: *"the background distiller does
not pass it. In the shipped pipeline the flag never appears."*

The `[boundary]` flag — the thing that stops restricted knowledge drifting toward a
less-restricted repo — has therefore never fired outside a hand-run `distill ingest`.

**Why it cannot be a one-liner.** The pipeline does not know which repo the session was
working in. `flags.add_flag` records `ts`, `session`, `kind`, `text` — and no cwd or repo.
So the fix is a schema addition plus plumbing:

1. `add_flag` records the originating directory (and/or the registered plugin containing
   it, via `routing.plugin_for_path`). Backwards compatible; absent on old flags.
2. `mneme-distill-pipeline` derives `--source-plugin` from the flags snapshot it already
   passes as `--flags-snapshot "$BUNDLE"`. Where flags disagree, the conservative answer is
   the MOST restricted of them, not the first.
3. Old flags with no recorded origin keep today's behaviour: unknown, and honestly labelled.

**Partial mitigation already shipped (Plan 14).** `staging.route` resolves the source from
the candidate's current target when the field is absent, and persists it — so a candidate
staged FOR a repo carries that repo's sensitivity through every subsequent hop, and the
laundering paths the review found are closed. What remains unprotected is a genuinely
unrouted candidate with no recorded origin, which is exactly what this item fixes.

## 2. Staging has no locking anywhere (MED)

Two concurrent `mneme share route` calls on one candidate produce **two** candidates, both
exiting 0 — read, write-new, unlink-old, with nothing serialising it. Reproduced with
threads and with a deterministic interleave. `grep -rn 'flock\|fcntl\|\.lock' core/` returns
nothing, so this is not specific to `route`: a route racing the distiller's `stage`, or two
`stage` runs from overlapping sessions (the Stop and PreCompact hooks can both fire), have
the same shape.

`ensure-sqlcl.sh` in a sibling repo solves exactly this problem with an atomic lock that
self-heals when its recorded holder is gone. The same shape belongs in `paths`, used by
`write_candidate`, `route`, `decline`, and the flag consume-and-clear path.

Not urgent — these are human-driven interactive commands — but it is a correctness gap that
will not announce itself when it bites.

## 3. A route is invisible to the distiller, so the next run re-stages the mis-route (MED)

Routing re-mints the id, which stops the distiller staging a twin of the CORRECTED
candidate. It does nothing about the mis-route itself: the distiller's routing guess has not
changed, so the next ingest stages the same sentence for the original target again, and the
gate shows it under two targets. Approving both puts one sentence in two repos.

Nothing records that a human rejected that destination. The declined ledger is the obvious
model — `_applies_to` already scopes a verdict to one plugin, which is precisely "not for
THIS repo" — but a route is not a decline of the knowledge, and reusing the ledger would
block routing it back later. Options worth weighing:

- a `routed.jsonl` the stage path consults, keyed like the declined ledger;
- recording the rejected destination on the candidate and having `stage` honour it;
- feeding routing corrections back to the distiller as routing hints, which is the version
  that actually improves the guess rather than suppressing its output.

Related: `docs/superpowers/plans/2026-08-23-plan-14-reroute.md`.
