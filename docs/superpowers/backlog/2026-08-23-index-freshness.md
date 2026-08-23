# Backlog: the index goes stale silently — `/mneme:index`, or rebuild when it matters

**Status:** raised 2026-08-23 (Rick). Address after the current work.

## The ask

There is no `/mneme:index` command, and `mneme index rebuild` is something a user has to
know to run. Either give it a slash command, or rebuild automatically when the index is out
of date.

## Why this bites, concretely

The index is what `mneme search` and the `retrieval` skill read. It is built from the
**working trees** of registered repos, so it goes stale on every event that changes one —
and almost none of those events are mneme's own:

- a maintainer merges a knowledge PR (mneme never does this; PR-only means a human always
  does), so the freshly merged facts are invisible to search until someone rebuilds;
- `git pull` in any registered repo;
- `mneme adopt`, `mneme migrate`, a classify or review finalize;
- a repo being registered at all.

The failure is silent and it is the worst kind for this product: `mneme search` answers
confidently with an out-of-date corpus, so the agent concludes the organisation does not
know something it merged last week. That is precisely the failure mneme exists to prevent.
It happened in this session — after merging PR #4 into `oracle-ai-dev` the index still held
the pre-merge shape until a manual `index rebuild`.

## The two shapes, and why the second is better

**A `/mneme:index` command** is a half-hour of work and makes the operation discoverable.
It does not fix the problem: a user who does not know the index can be stale does not know
to run it, and the whole point is that the staleness is invisible.

**Rebuild when it matters** is the real answer, and it needs a cheap staleness signal so
that a read path can check it without paying for a rebuild every time. A candidate:

1. Record, per registered repo, the `HEAD` sha and the mtime of the knowledge root at build
   time — `mneme_index.db` already has a `plugins` table with `built_at`.
2. On a read (`search`, `context`, the retrieval skill's lookup), compare `git rev-parse
   HEAD` per registered repo — one cheap subprocess each, and the count is small.
3. Rebuild only the repos whose sha moved (`index_tree` is already per-plugin), then answer.
4. `mneme index rebuild` keeps working as the force option, and `--check` reports staleness
   without rebuilding for scripting and CI.

The per-repo granularity matters: a full rebuild across every registered repo on every
search would be the kind of cost that gets switched off.

## Decisions to make first

1. **Is a read allowed to write?** Auto-rebuild means `mneme search` mutates
   `~/.mneme/index.db`. That is fine for a local cache but it interacts with the missing
   lock (see `2026-08-23-boundary-and-staging-integrity.md`, item 2): two searches racing a
   rebuild is exactly the shape that has no protection today. This item probably wants that
   lock to land first, or a build-to-temp-and-rename.
2. **A dirty working tree.** A repo mid-classify has uncommitted edits, and `HEAD` has not
   moved. Either index the working tree and use a content signal rather than the sha, or
   accept that a rebuild mid-pass shows the pass's own edits. The current build reads the
   working tree, so the sha alone is not a complete signal.
3. **Failure policy.** A registered repo whose clone is missing or unreadable must not make
   `search` fail — degrade to the stale answer and SAY the corpus is stale, rather than
   erroring or silently answering short.
4. **Say when it happened.** Whatever the trigger, `search` should be able to report "index
   rebuilt for 2 repos" so a user can see the freshness they are getting, and `mneme status`
   should show which repos are behind rather than just `built_at`.

Related: `docs/superpowers/backlog/2026-08-23-boundary-and-staging-integrity.md` (locking).
