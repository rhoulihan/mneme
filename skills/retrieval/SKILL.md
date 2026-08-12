---
name: retrieval
description: Use when you need institutional knowledge that installed knowledge plugins might already hold — a procedure you half-remember exists, a constraint or gotcha about a system named in the task, or before designing something a team may have solved. Searches the mneme index by vague notion.
---

Before reinventing, check what the organization already knows.

1. Resolve the binary: `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` if `CLAUDE_PLUGIN_ROOT` is set, else `bin/mneme`.
2. Run `mneme search "<a few words describing the need>"` — terms are OR-matched and ranked, so cast wide. Filter with `--kind skill` or `--kind fact` and `--plugin <name>` when the target is known.
3. Top hits are entry points: skills route onward through their own SKILL.md; facts carry their category, tags, and verified date. For structured lookups use `mneme db query "SELECT ... FROM units WHERE ..."` (read-only).
4. If the index is not built (`index not built` on stderr), fall back to reading the registered plugins' files directly — `mneme registry list` shows their paths — and suggest `mneme db enable` to the user once.
5. If retrieved knowledge turns out wrong or stale, flag it: `mneme flag --kind knowledge-issue "<what is wrong>"`.
