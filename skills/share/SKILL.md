---
name: share
description: Review staged knowledge candidates and harvest the approved ones into their knowledge plugins as commits or pull requests. This is the human gate — nothing is shared without explicit approval here.
disable-model-invocation: true
---

You are driving mneme's harvest gate. The CLI (binary at `"$CLAUDE_PLUGIN_ROOT/bin/mneme"` when installed, else `bin/mneme`) does all mechanical work — never edit knowledge repos, staging files, or git state directly.

1. Run `mneme share list` and present the queue grouped by target plugin: id, type/edit, confidence, and every annotation. Call out `[boundary]` flags (candidate routed toward a less-restricted repo — the user must explicitly confirm those) and `[similar: <unit>]` flags (possible duplicate of existing knowledge — suggest comparing before approving). `[QUARANTINED]` candidates (visible with `--all`) contain secret-scan hits and CANNOT be applied; they need redaction first.
2. For each candidate the user wants to inspect, run `mneme share diff <id>` and show the content (new units whole, updates as diffs).
3. Collect decisions conversationally. Rejections: run `mneme decline <id> --reason "<their reason>"` — the reason matters; the distiller uses the ledger to never re-propose it.
4. Approvals: run `mneme share apply --ids <id1>,<id2>,...` (add `--no-push` if the user wants local-only). Report each result line and any PR URL or manual-push instruction verbatim.
5. If a candidate is routed to the wrong plugin, do not apply it — tell the user re-routing lands in a future release and offer decline-and-reflag instead.

Never apply candidates the user has not explicitly approved in this conversation.
