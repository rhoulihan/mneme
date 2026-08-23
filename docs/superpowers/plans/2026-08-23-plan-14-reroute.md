# Plan 14 — re-route a staged candidate

**Goal:** move a staged candidate to a different target plugin, at the gate, without
declining it first.

**Why now:** `/mneme:share` tells a user that a mis-routed candidate cannot be fixed —
"re-routing lands in a future release" — and offers decline-and-reflag instead. That advice
is worse than it sounds. A decline is a human verdict recorded forever, and for a candidate
with **no destination** the ledger deliberately makes it GLOBAL (`staging._applies_to`:
"guessing a scope for them would resurrect knowledge a human has already rejected"). So
today the only sanctioned way to fix a routing mistake permanently silences the knowledge
in every repo. Two real candidates are sitting in the queue unrouted for exactly this
reason.

## The three things that make it non-trivial

1. **The id embeds the target.** `candidate_id(type, target, body)` hashes
   `target + "\n" + body`. Re-routing must re-mint the id, or the id stops being derivable
   from its inputs and the next distiller run stages the same knowledge again under the
   correct id — a duplicate the gate shows twice.
2. **The declined ledger is target-scoped.** A body declined for the NEW target must not be
   re-admitted by routing it there; "declined stays declined" is a spec §7.3 guarantee.
3. **The boundary check needs the source context's sensitivity**, which is computed at
   distill time (`cli` calls `routing.boundary_warning(source_scope.sensitivity, target)`)
   and then thrown away — only the rendered string is stored. Re-routing to a *less*
   restricted repo is precisely the move that flag exists to catch, so it has to be
   recomputable.

## Task 1: record what the boundary check needs

**Files:** `core/mneme_core/staging.py`, `core/mneme_core/cli.py`;
`tests/core/test_staging_roundtrip.py`

`Candidate.source_sensitivity: str = ""` — the sensitivity of the context the knowledge was
captured IN, persisted at stage time. Backwards compatible: absent on every candidate
staged before this, and every reader treats "" as unknown.

- [x] **Steps:** failing test (round-trips through `_to_text`/`_from_text`, absent field
  reads as "") → set it at the distill call site → verify old staged files still load.

## Task 2: `mneme share route <id> --target <name>`

**Files:** `core/mneme_core/staging.py`, `core/mneme_core/cli.py`, `skills/share/SKILL.md`;
`tests/core/test_share_route.py` (new)

**Interfaces:** `staging.route(home, cand_id, target, *, allow_boundary=False) -> Candidate`

Refuses, each with a message naming the fix:
- unknown candidate id;
- target not registered (and `unassigned` IS a legal target — un-routing is allowed);
- target unchanged (nothing to do, not an error worth a stack trace);
- the body is already declined **for the new target** — declined stays declined;
- a candidate with the re-minted id already exists — that is the same knowledge already
  headed there, so this is a duplicate, not a move;
- the move crosses a sensitivity boundary and `--allow-boundary` was not given.

On success: re-mints the id, recomputes `boundary_warning` against the new target from
`source_sensitivity` (falling back to the CURRENT target's sensitivity when the field is
absent, since the knowledge was judged fit for that repo, and to "unverified" when neither
is known), writes the new file, removes the old one, and returns the new candidate.
Quarantined candidates may be re-routed and stay quarantined — routing does not launder a
secret-scan hit.

- [x] **Steps:** failing tests for every refusal and the happy path → implement →
  mutation-verify → adversarial review round → commit.

## Task 3: the gate offers it

**Files:** `skills/share/SKILL.md`, `docs/getting-started.md`, `README.md`

The share skill currently instructs the agent to offer decline-and-reflag. It should offer
`route` instead, and say plainly that declining an unassigned candidate silences it
everywhere — which is the fact that makes routing-before-declining matter.

- [x] **Steps:** update the skill contract, then the docs.

## Adversarial review — 2026-08-23

Eight findings. The two HIGH ones were the same defect seen from both ends, and the review
was right that it defeated the feature's own safety story:

- **`restricted -> unassigned -> public` completed with no refusal**, one command after the
  direct move was refused. `_boundary_for_move` treated `unassigned` as a free pass AND
  `route` never persisted the source it had just inferred, so the first hop erased the
  evidence. Fixed: the resolved source is carried on every move, `unassigned` preserves it,
  and a recorded-but-unresolved crossing outranks the current target (otherwise a legacy
  candidate sitting in a public repo resolved to `public` and lost its flag).
- **In the shipped pipeline every candidate has an empty `source_sensitivity`**, because
  `bin/mneme-distill-pipeline` never passes `--source-plugin` — pre-existing, and the reason
  the first finding was not an edge case. Not fixable here: flags do not record where they
  came from. See `docs/superpowers/backlog/2026-08-23-boundary-and-staging-integrity.md`.

Also fixed: a cross-repo move of an `edit=update` candidate is refused, because
`target_unit` names a unit in the repo it is leaving — carried across it either aborts the
whole harvest batch or, on a topic-key collision, silently rewrites an unrelated bullet. A
stale `similar_to` hint is dropped on a cross-repo move, and an empty `--target` says so.

Backlogged rather than fixed, both pre-existing and neither specific to routing: staging has
no locking (two concurrent routes yield two candidates), and a route is invisible to the
distiller, so the next run re-stages the mis-route under the original target.

## Verification

1. The two real unassigned candidates in Rick's queue can be routed to a repo and applied.
2. A decline recorded for the destination still blocks the move.
3. A move into a less-restricted repo warns, and refuses without `--allow-boundary`.
4. The re-minted id equals `candidate_id(type, new_target, body)` — so the next distiller
   run dedupes against it instead of staging a twin.
5. Candidates staged before `source_sensitivity` existed still load and still route.
