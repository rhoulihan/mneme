# mneme — Automatic Capture Triggering (revision B)

**Original requirements:** Rick Houlihan, 2026-09-18 (`mneme-automatic-capture-requirements.md`)
**This revision:** 2026-09-18, after probing the installed Claude Code (2.1.278)
**Status:** draft for implementation
**Component:** mneme plugin — capture layer

---

## 0. What changed, and why

Revision A's §5 mapped each requirement onto a hook event. The document itself said:
*"Verify current hook event names against the installed Claude Code version before
implementing — this list is from working knowledge, not from the shipped schema."*

That was the right instinct. The documentation says four of the five mappings cannot work.
**Then a live probe against Claude Code 2.1.278 showed the documentation is itself wrong on
two counts, in mneme's favour.** Both matter enough that neither the original plan nor the
plan derived from the docs is the right one.

### 0.1 What the probe did

A throwaway settings file registered a payload-dumping hook on `PostToolUse`,
`PostToolUseFailure`, `SubagentStop`, `Stop` and `PreCompact`, plus an injecting hook on
`UserPromptSubmit`. A headless session (`claude -p`) then ran a failing command, a
succeeding command, and a subagent. Every payload was captured verbatim.

### 0.2 Verified platform facts

| Question | Documentation says | **Probe found** |
|---|---|---|
| Does `PostToolUse` carry the tool's result? | **No** — "hooks can't undo actions since the tool has already executed" | **Yes.** Full `tool_response` with `stdout`, `stderr`, `interrupted`, plus `tool_use_id` and `duration_ms` |
| Is there a failure event? | `PostToolUseFailure` listed, undetailed | **Yes, fires separately**, carrying `error` (e.g. `"Exit code 1"`), `tool_input`, `is_interrupt`, `duration_ms` |
| Can `SubagentStop` see the subagent's report? | Documented as cleanup/side-effects only | **Yes.** `last_assistant_message` holds the report text, plus `agent_transcript_path`, `agent_id`, `agent_type` |
| Can `SubagentStop` inject into the parent? | No | **No** — confirmed. It can *see*, not *speak* |
| Can `Stop` inject context? | No (command hooks) | **No.** Its stdout is discarded entirely in headless mode |
| Can `UserPromptSubmit` inject? | Yes, `hookSpecificOutput.additionalContext` | **Yes — verified end to end.** Injected text demonstrably reached the model's answer |

Every payload also carries `session_id`, `cwd`, `permission_mode`, `prompt_id` and
`transcript_path`.

### 0.3 The two consequences

**The payloads are rich enough that detection needs no transcript parser.** Revision A
assumed hooks could prompt; the docs suggested hooks could barely see. Neither is true: hooks
see almost everything and can prompt almost nowhere.

**`UserPromptSubmit` is the only in-session delivery channel, and that is an improvement.**
Revision A's RC3 says *"'at the moment it happens' is the worst moment"* — the flag competes
with processing the finding. A prompt delivered at the next user turn lands at a **turn
boundary instead of mid-analysis**, which is the fix RC3 asks for. It is also inherently
rate-limited to one per user turn, which largely dissolves N2.

---

## 1. Problem and root cause — unchanged

Revision A §1 stands as written. Capture is passive, it loses every scheduling contest to the
foreground loop, and **RC4 — omission is silent — is the load-bearing factor.**

One addition, from mneme's own source. `hooks/scripts/distill-hook.sh` runs on `Stop` and
contains:

```bash
"$ROOT/bin/mneme" distill pending >/dev/null 2>&1 || exit 0
```

`mneme distill pending` prints the pending flag count and exits 1 when that count is zero. So
on exactly the session RC4 describes, **mneme computes the zero, redirects it to `/dev/null`,
and exits silently.** Every flag record already carries a `session` field. The tally R3 asks
for is not new infrastructure; it is a number that already crosses that line and is thrown
away.

---

## 2. Design principle — amended

Revision A:

> **Capture must be bound to an event that already happens, and its absence must be visible.**

Both clauses hold. One corollary is added, forced by §0.2:

> **Seeing and speaking are different events.** Detection binds to the event where the
> knowledge appears (`SubagentStop`, `PostToolUseFailure`). Delivery binds to the only event
> that can speak (`UserPromptSubmit`). A design that requires one hook to do both cannot be
> built.

And one is sharpened. Revision A's *"cheap or it loses"* was about authoring friction; it is
also a hard runtime constraint:

| Operation | Measured cost |
|---|---|
| One mneme hook invocation (bash + python3 + CLI) | **264 ms** |
| `import mneme_core.cli` alone | 153 ms |
| Bare `python3 -c pass` | 10 ms |
| Shell-only append to a file | **0.02 ms** |

Hooks block the turn unless marked `async`, and they run in parallel with each other. A
264 ms hook on every Bash call costs over two minutes across a 500-call session. **The hot
path must not import `mneme_core`.** This is the same rule mneme already applies to locking:
*a write-shaped wait on the agent's hot path is worse than the race it prevents.*

---

## 3. Functional requirements

### R1 — Event-bound capture (revised)

mneme SHALL separate **recording**, **analysis** and **delivery** across three classes of
hook, because no single event can do more than one of them.

| Class | Events | Cost budget | Job |
|---|---|---|---|
| **Record** | `PostToolUse`, `PostToolUseFailure` (matcher `Bash`) | shell only, no Python | Append the raw payload to a session ring buffer |
| **Analyse** | `SubagentStop`, `Stop`, `PreCompact` | full mneme, `async` where possible | Detect candidates, write them pending |
| **Deliver** | `UserPromptSubmit`, `SessionStart` | one per user turn | Inject pending candidates and the tally |

Revised trigger mapping:

| Rev A | Status | Revision B |
|---|---|---|
| **T1** `SubagentStop` → inject candidates | Injection impossible | `SubagentStop` **reads `last_assistant_message`** (the report), detects, writes pending. Delivery at next `UserPromptSubmit` |
| **T2** `PostToolUse` → fail→fail→success | Rescued | `PostToolUseFailure` and `PostToolUse` are **separate events**; `tool_input.command` appears on both. The transition is directly observable |
| **T3** `Stop` → tally | Cannot inject; stdout discarded | `Stop` **writes** the tally; `SessionStart`/`UserPromptSubmit` surfaces it |
| **T4** `PreCompact` → last chance | Cannot inject | `PreCompact` analyses and writes pending; delivery deferred to the next prompt |

**T1 remains the highest-value trigger** and is now directly implementable: the subagent's
report arrives as a string in the payload.

### R2 — Automatic candidate detection (revised input)

Signals are unchanged from Revision A §R2 — vendor error codes followed by resolution,
negation-of-expectation language, measured constants, contradiction of installed knowledge,
and a command retried with differing arguments.

**What changes is the detector's input contract.** The detector SHALL consume a *normalised
event record*, not a hook payload, with two adapters:

- **Production adapter** — hook payloads (`tool_input`, `tool_response`, `error`,
  `last_assistant_message`).
- **Replay adapter** — a session transcript JSONL.

This is what makes acceptance criterion 5 buildable: the `pg-oracle-bench` transcript
predates the ring buffer, so it can only be replayed from the transcript, while production
reads payloads. One detector, two adapters, and the regression suite exercises the same
detection code the product runs.

**Precision over recall** stands: a noisy detector will be disabled within a day.

### R3 — Visible tally (revised delivery)

Unchanged in substance. Delivery changes because `Stop` cannot speak:

- `Stop` writes `{session_id, tool_calls, flags, candidates_detected, candidates_unflagged}`.
- `SessionStart` — where mneme **already injects a brief** — surfaces the previous session's
  line.
- `UserPromptSubmit` surfaces the current session's running tally when it is non-trivial.
- A session exceeding the work threshold (default 20 tool calls) with **zero** flags SHALL be
  reported explicitly, naming detected-but-unflagged candidates so a deliberate zero is
  distinguishable from an oversight.

Do **not** use `SessionEnd`: it has a **1.5-second budget shared across all hooks**, and
mneme's import alone is 264 ms before any analysis.

### R4 — Artifact binding

Unchanged from Revision A. The `Ruling: … / Flags: N` convention works because the artifact
write is already mandatory and already survives compaction. **Rulings and flags are the same
moment.**

### R5 — Knowledge-contradiction detection (mechanism identified)

Revision A's open question 4 asked whether this is detectable without false positives.
Revision A also answered it, in passing: *"contradicting one's own retrieved context is
precisely what a model is worst at."* That rules out a model-based detector, not a mechanical
one.

**The mechanism already exists.** `cli._distill_ingest` computes `similar_to` for every
candidate by probing the FTS index for its nearest installed fact. R5 is therefore: compare a
candidate against its nearest indexed neighbour and raise `knowledge-issue` when they share a
subject but differ by a negation or a constant. The index does what the model cannot.

### R6 — Zero-friction flag path

Unchanged in requirement, and now with a confirmed mechanism: a plugin may declare
`mcpServers` alongside `hooks` in its manifest, and both merge.

**This is the same `mneme-mcp` server already designed in
`2026-09-11-funes-knowledge-repo.md`.** That spec's D12 deliberately gated the MCP surface to
Funes, because a model-invocable *write path into a knowledge repo* would bypass the
`disable-model-invocation: true` that protects `/mneme:share` on ten of eleven skills.

`mneme_flag` does not have that problem, and the distinction is exact: **a flag is a note,
not a write to a knowledge repo.** The human gate remains where it is, at share time,
unchanged — so a model-invocable flag tool bypasses nothing. R6 is the general-purpose
justification for the surface D12 withheld, and it is safe on precisely the grounds D12 used
to withhold the other one.

Note for the record: MCP tools have **no** native equivalent of `disable-model-invocation`.
The only documented control is a `PreToolUse` hook that refuses the call, or not exposing the
tool. That is exactly why the flag/write distinction above has to be argued rather than
assumed.

---

## 4. Non-functional requirements

N1 (no mid-session distillation), N3 (exclusion-aware), N4 (silent when idle) and N5
(degradable) are unchanged.

**N2 is revised.** Revision A budgeted one prompt per N tool calls. Two problems: a tool-call
budget fires during long mechanical stretches and stays silent during dense analysis —
backwards on both counts — and it is now largely redundant, since `UserPromptSubmit` fires at
most once per user turn by construction. The budget SHOULD instead be expressed per *detected
candidate*, with a cooldown, and the channel supplies the rest.

**N6 (new) — the hot path stays out of Python.** The `Record` class of hook SHALL NOT import
`mneme_core`. Appending the raw payload JSON and analysing it later is the same
record-now/process-later split mneme already uses for flags and distillation.

---

## 5. Implementation

| Req | Event | Payload fields used | Notes |
|---|---|---|---|
| T1 | `SubagentStop` | `last_assistant_message`, `agent_type`, `agent_transcript_path` | Report text is in the payload; no transcript read needed for the common case |
| T2 | `PostToolUse` + `PostToolUseFailure`, matcher `Bash` | `tool_input.command`, `tool_response.stdout/stderr`, `error` | Two events give the fail→success transition directly |
| T3 | `Stop` | `session_id`, flag count from `distill pending` | Writes; does not speak |
| T4 | `PreCompact` | `transcript_path` | Analyse and persist before the window closes |
| R3/R1 delivery | `UserPromptSubmit` | — | `hookSpecificOutput.additionalContext`; **verified working** |
| R6 | `mcpServers` in `plugin.json` | — | `mneme_flag` as a structured tool, no shell quoting |

mneme's existing `Stop`/`PreCompact` hooks are already `async: true`. That is correct and
should stay — an async hook cannot block, and it also cannot inject, which is consistent with
delivery living elsewhere.

---

## 6. Acceptance criteria

1. A session producing ≥5 findings matching R2 signals results in ≥5 capture prompts,
   **without the user asking.**
2. A session producing zero such findings produces zero prompts.
3. A session ending with zero flags and >20 tool calls produces an explicit statement of that
   fact, delivered at the next prompt or session start.
4. A flag containing `$`, `"`, `'` and a newline can be submitted without escaping.
5. **Replaying the `pg-oracle-bench` transcript surfaces ≥12 of the 18 hand-captured facts** —
   through the same detector the production adapter feeds.
6. **(new)** A 500-Bash-call session adds **< 5 s** of aggregate hook latency.
7. **(new)** No hook in the `Record` class imports `mneme_core`.

Criterion 5 remains the real test: the transcript and the ground-truth flags both exist, so
this is a regression suite rather than a thought experiment.

### 6.1 First measurement against the corpus (2026-09-19)

The corpus is the `pg-compare` session — 5,034 tool calls, **106** hand-captured flags
(the requirements doc's "18" was one burst of a larger set). Recovering the ground truth
is itself mechanical: the flags were made with `mneme flag`, so the transcript carries both
the signal and the answer key.

Building it immediately paid for itself, finding four defects in code written hours earlier:

| Found | Effect before the fix |
|---|---|
| Flag counting was start-of-command anchored | 109 real invocations read as **1** — the real shape is `cd <repo>` then one flag per line |
| `_command_shape` did not strip `cd` prefixes | every command shared the shape `cd`; **521** junk signals pairing unrelated failures with unrelated successes |
| T2 implemented as 1 prior failure, not the spec's ≥2 | the rule fired **485** times in one session |
| `E[A-Z]{3,}` as an errno pattern | `ERROR`, `EMAIL`, `EVERY`, `EXIT` all read as vendor error codes |

After those fixes, per rule:

| Rule | Signals | Assessment |
|---|---|---|
| `resolved-error` | **21** | Plausible throughout, and includes `ORA-01000` — the cursor-exhaustion fact the requirements doc names by hand |
| `retried` | 91 | too noisy |
| `surprise` | 72 | fires on the assistant's own status prose, not on findings |
| `measured` | 82 | same |

**So only `resolved-error` is enabled by default.** R2's binding constraint is *precision
over recall — a noisy detector will be disabled by users within a day*, and 245 prompts in
one session is not a near miss. The other three rules are implemented, tested and opt-in
(`detect.ALL_RULES`), so enabling one is a decision with evidence attached.

### 6.2 After delivery was wired (same day)

Wiring `UserPromptSubmit` and running the loop end to end on the corpus exposed two more,
both of which had been sitting in the "21 plausible signals" above:

- **A vendor code in a command's OUTPUT was being read as that command failing.** So
  `cat server.log` printing `ORA-00942` recorded a failure of `cat`. The three candidates
  that actually reached the top of the delivery queue were
  `ERRCODE_INTERNAL_ERROR then a success of echo`, `AssertionError … of sed`, and
  `ERRCODE_ARRAY_SUBSCRIPT_ERROR … of sed` — the worst three in the set.
- **But requiring a non-zero exit instead throws away nearly everything real.** Only
  **83 of 5,095** results in the corpus set `is_error`, because a database error arrives
  with exit code 0: `docker exec … sqlplus` succeeds while printing `ORA-00942`. That rule
  produced **zero** signals.

The distinction that works is neither exit status nor output content but *what kind of
command it is*: a code in the output of a text-mover (`cat`, `sed`, `echo`, `grep`, `git`)
is something it displayed; a code in the output of anything else is a fault it hit.

Final on the corpus: **7 signals**, ordered hardest-won first, led by
`ORA-06550 then a success of docker exec, after 4 failures`. From 707.

**What the corpus says about the design.** Token-overlap recall against the 106 flags is
~20% even for the tuned detector, and the qualitative sample explains why: the ground-truth
flags are dense technical claims, while assistant prose in a long agentic session is
dominated by status reporting, which is what the prose rules match. The mechanical text
detector has a low precision ceiling on prose.

That is evidence for the requirements doc's own ranking rather than against it: **T1 is the
high-value trigger, and its value does not depend on R2 being clever.** The human produced
106 flags in minutes when simply *asked at the right moment*. The strong version of this
feature fires on the event (a subagent report returning, a compaction) and hands the model
a bounded, specific prompt about recent context — with `resolved-error` as a precise
supplement, not as the mechanism.

---

## 7. Out of scope

Unchanged: distillation, storage, retrieval and the plugin format; automatic flag *authoring*;
cross-session deduplication.

---

## 8. Open questions

1. **Persist detected-but-unflagged candidates across sessions?** Recommended **yes, in a
   dedicated `missed.jsonl`** — and explicitly **not** in staging, `declined.jsonl` or
   `routed.jsonl`. Every existing mneme ledger records a *human decision*, and that is the
   entire source of its authority: the declined ledger can permanently silence knowledge
   precisely because a person wrote it. A detector's guess is not a decision and must not sit
   where decisions live.
2. **Interruption budget** — superseded by N2 above; the channel is the budget.
3. **Should T1 fire for every subagent?** Now cheap to decide empirically: `SubagentStop`
   carries the report, so the threshold can be a property of the text rather than a guess.
4. **Is R5 detectable?** Mechanism identified (§R5). Open question becomes the false-positive
   rate of the `similar_to` comparison, measurable against the replay corpus.
5. **(new) Does `Stop`-hook stdout reach an interactive terminal?** The probe was headless,
   where it is discarded. If interactive rendering differs, R3 could also report in-session to
   the human. Worth one interactive test; the design does not depend on it.
6. **(new) Does `SubagentStop` fire for every agent type**, including plugin-defined ones and
   background tasks? The probe covered `general-purpose` only.

---

## 9. Sequencing

1. **R3 tally** — un-discard the number `distill-hook.sh` already computes; surface it at
   `SessionStart`. Cheapest item, makes the failure loud, and produces the base-rate data the
   detector needs.
2. **Detector + replay adapter** — validated against the `pg-oracle-bench` ground truth
   *before* it is wired to anything live.
3. **R6 flag tool** — `mneme-mcp`, shared with the Funes work.
4. **`UserPromptSubmit` delivery** — the single channel.
5. **T1**, then **T2** with the record/analyse split.

Steps 1 and 2 are independently useful and neither can regress a session: one writes a file,
the other reads a transcript.
