# Plan 15 — the index stops going stale silently

**Goal:** a user of `mneme search` can never be answered confidently from an out-of-date
corpus without being told.

## The failure, precisely

The index is built from the **working trees** of registered repos, and nearly every event
that changes one is somebody else's: a maintainer merging a knowledge PR most of all, since
PR-only means a human always does the merging. mneme is not running then, so nothing marks
the index dirty. `mneme search` answers from the stale corpus and the agent concludes the
organisation does not know something it merged last week — which is the exact failure mneme
exists to prevent. It happened in this session: after PR #4 merged into `oracle-ai-dev`, the
index held the pre-merge shape until a manual rebuild.

## The decision the backlog left open, now answered by the code

**A read is NOT allowed to write.** `cli._require_index_db` opens the database with
`open_db_readonly`, and `_readonly_authorizer` denies `ATTACH`/`DETACH`/`PRAGMA` on top of
it — deliberate defence in depth. Auto-rebuilding inside `search` would undo that, put a
write on the hot path, and collide with the missing lock (there is no locking anywhere in
`core/`, so two searches racing a rebuild has no protection today).

So: **search DETECTS and REPORTS; rebuilding stays explicit and becomes cheap.** That is
also the better fix on its own terms — the bug is that the failure is *silent*, and turning
a silent wrong answer into a loud stale one is the whole win. An automatic rebuild would
merely hide the staleness faster.

## Task 1: a fingerprint that actually catches what changes

**Files:** `core/mneme_index/db.py`, `core/mneme_index/build.py`,
`core/mneme_core/indexing.py`; `tests/index/test_freshness.py` (new)

`git rev-parse HEAD` is NOT a sufficient signal: the build reads the WORKING TREE, so an
uncommitted edit mid-classify, an untracked fact file, or a `git checkout` of a branch all
change what the index should hold while `HEAD` may not move at all.

`indexing.fingerprint(root) -> str` — sha256 over the sorted `(relpath, content-hash)` of
exactly the files the index reads: `units.fact_files(root)` plus the `SKILL.md` of every
`units.readable_skill_dirs(root)`.

**Revised during implementation, and the mutation round is what caught it.** The plan said
`(relpath, size, mtime_ns)`, stat-only. Two mutants survived — dropping mtime, and dropping
the path — because every test changed a file's LENGTH. Writing the missing cases turned up a
genuine hole rather than a test gap: mtime granularity measured ~4ms on this machine, so an
edit within one tick of the previous write keeps its timestamp, and `"24 hours"` ->
`"48 hours"` keeps its size. A fingerprint that can miss an edit silently recreates the exact
bug this plan exists to fix.

Content also fixes the opposite error, which stat-only would have shipped: `git checkout` and
`git pull` rewrite mtimes without touching content, so a timestamp signal calls every pull a
change and burns a rebuild on nothing.

Measured before choosing: 13 files / 28 KB on a slow drvfs mount is ~32ms to read and hash
against ~19ms to stat. About 12ms for the guarantee, on a path a human invokes.

The `plugins` table gains `fingerprint TEXT NOT NULL DEFAULT ''`; `index_tree` records it.
An empty stored fingerprint means "indexed before this existed" and reads as stale once.

- [x] **Steps:** failing tests (edit / add / remove / touch-only / no-change) → implement →
  confirm an existing database migrates without a rebuild.

## Task 2: report it everywhere a human or agent looks

**Files:** `core/mneme_core/indexing.py`, `core/mneme_core/cli.py`

- `indexing.stale(home) -> list[StaleRepo]` — registered plugins whose fingerprint moved,
  are absent from the index, or whose clone is gone.
- `mneme index status` lists which repos are behind, not just `built_at`.
- `mneme index check` — exit 2 when stale, 0 when fresh, for scripting and CI.
- `mneme search` prints one warning line **to stderr** when stale, so stdout stays
  machine-parseable for every existing caller, and still returns the hits it has.
- `mneme status` names the count.

A missing clone degrades to the stale answer and SAYS so — it never makes `search` fail.

- [x] **Steps:** failing tests → implement → verify stdout is byte-identical when fresh.

## Task 3: rebuild only what moved

**Files:** `core/mneme_core/indexing.py`, `core/mneme_core/cli.py`

`rebuild(home, *, only_stale=False)`. `mneme index rebuild` keeps rebuilding everything
(existing behaviour, existing callers, and the force option); `--stale` does the incremental
pass. `index_tree` is already per-plugin and idempotent, so this is selection, not new
machinery.

- [x] **Steps:** failing test that a fresh repo is not re-indexed under `--stale` → implement.

## Task 4: the surfaces that make it discoverable

**Files:** `skills/index/SKILL.md` (new), `skills/retrieval/SKILL.md`, `README.md`,
`docs/getting-started.md`, `.claude-plugin/plugin.json` if commands are enumerated

`/mneme:index` — report freshness, rebuild what is stale, relay skipped units. And the
**retrieval** skill learns to check: if a search warns that the corpus is stale, say so
before answering from it, and offer the rebuild. That is the one place the warning reaches
the agent that was about to conclude "we do not know this".

- [x] **Steps:** skill contract → docs → verify the command inventory test still passes.

## Out of scope

Auto-rebuild from hooks (SessionStart could refresh in the background — real, but it is a
latency decision on every session start and wants the locking work first), and the locking
itself (`docs/superpowers/backlog/2026-08-23-boundary-and-staging-integrity.md`).

## Adversarial review — 2026-08-23

Ten findings. The central one broke the design's own reasoning and is worth stating in full:

> Proving the fingerprint's file set equals the BUILD's file set proves only that the two
> track each other. It says nothing about whether either tracks the REPO. **Agreement
> between two observers blinded the same way is not evidence of correctness.**

`Path.glob` returns `[]` for a directory it cannot read — it swallows the `PermissionError`
that `os.scandir` raises — so an unreadable facts directory looks empty to the build AND to
the fingerprint at once. Reproduced: the rebuild indexed zero facts and reported `0 skipped`
as a clean success, `index check` exited 0 saying "fresh", and `search` printed nothing on
stdout and nothing on stderr, while the repo's facts sat on disk. The invariant held
throughout.

Fixed by making blindness a *recorded fact* rather than an absence: every directory is
probed with `os.scandir`, what could not be read is hashed into the digest (so unreadable
can never collide with empty) and surfaced by both `rebuild` (as skipped) and `stale` (as a
reason).

Also fixed, all reproduced:

- **`stale()` failed open.** No database, a corrupt one, a future schema, or a writer
  holding the lock each reported "fresh" — so on a fresh install `index check` said fine
  with no database at all, and `rebuild --stale` did nothing, taking two invocations to
  converge. Every "cannot tell" is now reported as stale.
- **`fingerprint` raised on an unreadable parent**, so one bad repo broke `search`,
  `status` and `index check` outright — a regression against the parent commit, and against
  this function's own comment promising it never fails.
- **A de-registered repo's rows kept answering searches** and nothing reported it; removing
  the LAST plugin made it unfixable, because `rebuild` raised before pruning.
- **`except sqlite3.OperationalError` was too broad**: a lock read as "never indexed", the
  exact misdiagnosis the migration handling was written to avoid.
- **`search` opened the database twice**, so a lock in that window lost the warning while
  the hits still printed. One connection now.
- **Unbounded memory**: `read_bytes` on a 300 MB file allocated it whole, on the agent's
  hot path. Chunked reads — measured after: 300 MB file, 21 MB peak RSS, 0.14s.

**Not fixed, and honestly so.** The check reads the corpus on every `search`, so its cost is
O(bytes): the review measured ~2.6ms/file on a drvfs mount, which puts a 5 000-fact repo on
a Windows mount at several seconds per search. Real corpora today are two orders of
magnitude smaller (13 files / 28 KB on the largest registered repo; `index check` takes
0.4s including interpreter startup), and every cheaper signal reintroduces the ~4ms mtime
hole this design exists to close. Worth revisiting with a stat-prefilter plus a recorded
build timestamp if a corpus ever gets big enough to notice — not worth the machinery now.

## Verification

1. Merge a PR into a registered repo by hand; `search` warns and `index check` exits 2.
2. `--stale` rebuilds only the repo that moved.
3. A dirty working tree mid-classify reads as stale (the case `HEAD` alone would miss).
4. A database built before the fingerprint column loads, reads as stale once, then settles.
5. `search` stdout is unchanged when the index is fresh.
