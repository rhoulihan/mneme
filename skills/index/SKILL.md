---
name: index
description: Check whether mneme's search index still speaks for the registered repos, and rebuild the ones that have moved. Run it after merging a knowledge pull request, after pulling a registered repo, or whenever a search answer looks thinner than it should.
disable-model-invocation: true
---

The index is built from the **working trees** of registered repos, and almost every event that changes one belongs to somebody else — a maintainer merging a knowledge pull request most of all, since mneme never merges (PR-only means a human always does). mneme is not running then, so nothing marks the index dirty. Left alone, `mneme search` keeps answering from the old shape and an agent concludes the organisation does not know something it merged last week. That is the failure this command exists to catch.

The binary is `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`.

1. Check first: `mneme index check`. It exits **0** when fresh and **2** when stale, naming each repo and why — `changed since it was indexed`, `never indexed`, `local clone missing`, or `indexed before freshness tracking`. Exit 2 is a *report*, like `mneme verify`'s, not a crash.
2. If anything is stale, rebuild only what moved: `mneme index rebuild --stale`. Report each `indexed <plugin>: N skills, M facts, K skipped` line, and relay every `skipped:` line verbatim — a skipped unit is knowledge that will not be retrievable, and the reason names the file.
3. `mneme index rebuild` with no flag rebuilds everything. Use it when a fingerprint may be wrong rather than the tree — after upgrading mneme, or when `--stale` reported nothing but a search still looks short.
4. `mneme index status` shows per-repo counts, when each was built, and which are behind.

**A missing local clone is reported, never fatal.** It stays listed as stale until the clone comes back; `search` still answers with everything else it holds.

**mneme does not rebuild on its own during a search.** The search path opens the database read-only on purpose, so it tells you the corpus is stale and answers with what it has rather than writing on a read. If a search warned you, run step 2 and ask again — the second answer is the one to trust.
