# Funes knowledge repos — one pipeline, two front doors

**Date:** 2026-09-11 · **Status:** proposal (v3.1) — architecture decided, details for iteration
**Subject:** [FunesElMemorioso](https://github.com/MoraxAlvizo/FunesElMemorioso) (private) ↔ mneme v0.9.0
**Author:** Claude, at Rick Houlihan's direction
**Supersedes:** v1, v2 (2026-09-09) · **Companion:** `docs/superpowers/backlog/2026-09-11-ingest-hardening.md`

---

## 0. Revision log

**v1 → v2** (three-lens review). Corrections that still bind: **60/28** tools not 56/26; the
promotion payload is **`txn-lesson-payload-v4`** and v3 is refused; staging is **four**
tables; `rebuild_hvi` **does not exist** (`PKG_FUNES.sync_hvi` is live, exposed as no tool);
the composite-FK invariant holds only scoped-row → scoped-row; **there is no `mneme.toml`**; a
mneme **unit** is a skill file or *one fact bullet*; a **plain** repo makes `classify` refuse,
so a Funes knowledge repo must be **plugin mode**; "subscriber"/"service owner" were the
spec's construct, not Funes'.

**v2 → v3.** Rick and Omar settled the open question: the git repo stays the pipeline **and**
a `submit` verb funnels into it — *"committers install mneme, general users get a submit
verb… both get curated into the graph using the same process… the submit verb in funes is a
passthrough to `mneme:share`."* One staging system, one gate, one PR path.

**v3 → v3.1** (this revision, after a second review). v3 put the submit verb **inside Funes'
MCP adapter, shelling out to a local `mneme`**. That is wrong, and the Funes source says so:

> `mcp/oracle_funes_readonly_mcp_server.py:104-105` — `def _actor(): """Return a non-secret
> audit identity injected by the forced SSH command."""`, with `SSH_CONNECTION` supplying
> `source_ip`. The **write** server has none of this.

That asymmetry is the deployment model in code. The **write** server is local and
owner-operated (`bin/oracle-funes-mcp-stdio` sources `config/funes.env` — "credentials held
only on this host"). The **read-only** server is built to run **on the Funes host** behind an
sshd forced command, one SSH key per client, under one shared service account. Which means a
passthrough would exec `mneme` on the *Funes host*, in a shared `MNEME_HOME`, under a service
identity — the one place it must not run, and precisely the population it was for.

**The fix is to invert the hosting. mneme ships its own local stdio MCP server; the submit
verbs live there.** Funes keeps a pointer column and two small operations. Rick's design
intent is untouched — general users still get a submit verb, it still funnels into mneme,
both doors still curate through one process — only the hosting process changes, and Funes'
side gets *smaller*.

Also corrected in v3.1, all demonstrated against v0.9.0:

| v3 said | Actually |
|---|---|
| `submit_fact(scope_id, text, category, tags, evidence)` | `topic` is **required** and kebab-case; `evidence` has no slot and is **silently dropped**; `target` must be resolved from `scope_id` |
| "caps on every field" | `topic`, `name` and each **tag** are uncapped — a 100k topic + 200k tag wrote a **300 KB** candidate |
| the two-call handshake "**is** the human gate" | mneme's gate is protected by `disable-model-invocation: true` (10 of 11 skills). An MCP tool cannot have that. D3 is rewritten. |
| the invariant holds across both doors | the **boundary gate cannot fire** on the submit path — `--source` is not `--source-plugin`. Replaced by a gate-coverage matrix. |
| "opens a PR under the user's git identity" | every commit is hardcoded `user.name=mneme / mneme@localhost` (`gitops.py:31-32`) |
| `confirm_submission` returns a PR URL | `open_pr` returns **prose** on any failure and the caller cannot tell (`gitops.py:206-219`) |
| fork-and-PR | **no fork machinery exists**; `push_branch` is `git push -u origin` |
| registry fields additive "by construction" | `save_registry` **silently drops** unknown keys on every write → backlog item 4 |
| a rejection ledger is "missing" | mneme **has** one (`declined.jsonl`, scoped per target). What is missing is learning about an **upstream** PR rejection. |
| `Bullet.verified` | the class is `FactBullet`; `verified` is a **string**, and `mneme verify --days N` already *is* this gate |
| `/mneme:curate` is a "command" | there is no `commands/` directory; these are **skills** |

---

## 1. The ask

Users of Funes should be able to capture knowledge their scope's graph did not have, and get
it into that graph. Contribution flows to a scope-specific knowledge repo; the repo's or
service's owner curates it in.

---

## 2. What Funes is

*"All behaviour lives in the database — `PKG_FUNES` owns every rule — and everything outside
Oracle is either a thin stdio transport (`mcp/`), a build-time text transform, or a test
harness."*

| Server | Tools | Transactions | **Runs where** |
|---|---|---|---|
| `oracle-funes` | 60 | commits only on `status: "OK"` | **the owner's machine**, `config/funes.env` |
| `oracle-funes-readonly` | 28 | *"never registers a mutation tool, and always rolls its session back"* | **the Funes host**, behind an sshd forced command |

That last column is §0's finding and the hinge of the whole design.

**Data model.** `FUNES_SCOPES` is *"the only isolation boundary… Caller-chosen `scope_id
VARCHAR2(128)`; **no status, owner, or expiry**."* `FUNES_LESSONS` is the retrieval unit;
`lesson_key VARCHAR2(1024)` is the caller's reference; status defaults to `PROVISIONAL`.
`FUNES_ENTITIES` identity is `UQ (scope_id, canonical_key)` — *"ENTITY_NAME is the mutable
human-readable label. CANONICAL_KEY is the identity"* (`sql/05_graph.sql:16-18`).
`FUNES_FACTS` is bitemporal. Ontology is global.

**`LESSON_TEXT_V1`** (`sql/07_pkg_funes.sql:263-275`): `summary` 12–1200; `concepts` 1–12 ×
2–80; `learnings` **min 1, max 10** × 12–600, kind one of
`decision|gotcha|invariant|how-to|incident`; optional `narrative`; `additionalProperties:
false` everywhere. A lesson may promote with no relationships.

**The learning loop** (write server only): `start_learning` → `push_lesson`/`push_entity`/
`push_fact` → `list_draft_items` → `promote_lesson` → `end_learning`.

**Two stale documents.** `schemas/operations_contract_v1.md` describes `register_document`,
`import_curated_document`, `ingest_and_curate_document` and the curation-run lifecycle — none
of which exists. `ARCHITECTURE.md` carries its own staleness banner; the operations contract
does not, and `schemas/README.md` still calls it *"the authoritative Phase 3 public
contract."*

---

## 3. The gap

`oracle-funes-readonly` registers no writer, so anyone holding only it cannot contribute
through MCP. That is the mechanical fact and it is enough.

What Funes records about the shape of the hole (`ARCHITECTURE.md:487-489`):

> There is no `list_documents`, `list_lessons`, or `list_entities`. A scope's contents cannot
> be inventoried through the API — **which also blocks any review-then-promote or cleanup
> workflow.**

Funes names *review-then-promote* as a workflow its API blocks — which is why the review
record lives in git.

On 2026-08-31 Omar replaced a `SUBMITTED` state and a `cure` operation with in-session agent
promotion — *"so knowledge is immediately available to other sessions."* **The gate went for
latency, not trust.** An outside contribution is already asynchronous, so that reason does not
extend to it.

---

## 4. The architecture

### 4.1 Two front doors, and a gate-coverage matrix instead of a slogan

v3 claimed an invariant — one staging system, one gate, one PR path, one curation path. The
first, third and fourth hold. **The second does not**, and asserting it hid a real hole, so it
is replaced by a table.

```
   general user                                committer / curator
   (read-only Funes on the Funes host)         (write server, local)
         │                                              │
   mneme-mcp: submit_fact ──┐          ┌──── mneme flag / distiller
   (LOCAL, user's machine)  │          │
                            ▼          ▼
                    ┌──────────────────────────┐
                    │  mneme staging           │  ONE store
                    └────────────┬─────────────┘
                                 ▼
                        machine gates (see matrix)
                                 ▼
        confirm_submission ──────┴────── /mneme:share
        (MCP elicitation)                (disable-model-invocation)
                                 ▼
                    PR → scope knowledge repo (a mneme plugin)
                                 ▼
                    review (CODEOWNERS) → merge → /mneme:curate → graph
```

| Gate | `/mneme:share` | `submit_fact` | Note |
|---|---|---|---|
| secret scan (ingest) | ✅ | ✅ | `cli.py:1187` |
| secret re-scan (apply) | ✅ | ✅ | `harvest.py:399-401` |
| lint | at apply | **must move to preview** | else preview-passes/confirm-fails is normal |
| boundary / sensitivity | ✅ | **✅ only if the door records cwd** | §4.3 |
| duplicate `[similar]` hint | if indexed | **absent until `mneme index` runs** | `cli.py:1148-1157` |
| declined ledger | ✅ | ✅ | already per-target |
| topic-key collision | at apply, aborts batch | at apply, aborts batch | `harvest.py:224-228` |
| human approval | user-initiated skill | model-invocable tool | **not equivalent — §4.4** |

### 4.2 mneme ships a local MCP server

mneme has no MCP surface today. This is the natural place for one, and it puts the verbs where
the user is.

`mneme-mcp` — stdio, on the user's machine, as the user, against their `MNEME_HOME`, git
identity and `gh` token:

- `submit_fact(scope_id, topic, text, category, tags)` → stages; returns candidate id, the
  **rendered bullet**, target repo, and findings.
- `confirm_submission(candidate_id)` → gate + PR.
- `list_pending(scope_id)` → what is staged and unconfirmed.

`topic` is in the signature because it is **required and kebab-case** (`proposals.py:107-108`)
and is the destination *filename*; omitting it means an LLM silently picks which file of
someone else's repo a fact lands in. `evidence` is **not** in the signature: the proposals
schema has no slot and `_validate` drops unknown keys silently, so accepting it would discard
the field the curator needs at promotion. Adding `evidence` to the schema is the alternative
and is a Phase-2 decision.

Everything runs **in-process**, so §4.5's CLI-as-API problem mostly dissolves and the adapter
can resolve `scope_id → target` from the user's own registry.

This design also depends on `distill ingest` being fixed first — it exits 0 when every
proposal was rejected, emits no candidate id or bullet, and leaves `topic`/`name`/tags
uncapped. All four are filed in `backlog/2026-09-11-ingest-hardening.md` and are
**prerequisites**, not nice-to-haves.

### 4.3 Funes' side, which is now smaller

| Surface | Where | Note |
|---|---|---|
| `feedback_repo_url`, `feedback_enabled` on `FUNES_SCOPES` | `sql/01` | or a `FUNES_SCOPE_FEEDBACK` side table |
| `get_feedback_binding(scope_id)` | **both servers**, read | returns the pointer + the scope's lesson-kind definition |
| `set_feedback_repo(scope_id, url)` | **write server only** | v3 named no writer at all |

No exec, no local binary, no doctrine exception: Funes stays PL/SQL behind thin transports.

**This is a convention, not an authorization anchor.** v3 called it one; `FUNES_SCOPES` has no
owner and both servers connect with the same credentials and grants, so "the scope owner" means
*whoever holds `config/funes.env`* until Funes has a principal model. What the pointer buys is
real but smaller: a `lesson_key` naming a repo that is not the published one is **detectably
foreign**, and a contributor can discover where to contribute — which closes cold start, the
problem v2 left open.

The boundary gate needs the door to record where the knowledge came from. `submit_fact` records
the caller's cwd exactly as `flags.add_flag` does, and resolves it through
`routing.plugin_for_path` — the mechanism shipped in v0.9.0 — so `--source-plugin` is derived
rather than absent. Without this the boundary check is structurally dead on the submit path,
which matters most for the population most likely to be moving an employer's knowledge.

### 4.4 The human gate, honestly

Ten of mneme's eleven skills carry `disable-model-invocation: true`, `share` among them. The
gate is **user-initiated only** — the model cannot open it. `skills/share/SKILL.md` says "Never
apply candidates the user has not explicitly approved in this conversation," and that
instruction is credible *because the model did not open the door.*

An MCP tool is model-invocable by construction. So `confirm_submission` gives that up, and v3's
claim that a preview-and-confirm "**is** the human gate" was wrong. The likely trajectory is
`submit_fact` → preview lands in a tool-result block the user may never expand →
`confirm_submission` in the same turn → "Done, here's your PR."

Compensating controls, in order of strength:

1. **MCP elicitation** — `confirm_submission` asks the *client* to render the preview and
   return the user's answer as a protocol event, not a model token. The nearest thing MCP has
   to `disable-model-invocation`. Requires client support; **gate the feature on it.**
2. **Refuse a `confirm_submission` in the same assistant turn as its `submit_fact`.**
3. **Echo-back**: `submit_fact` returns a short phrase `confirm_submission` must repeat,
   forcing the preview into the model's visible output. Weak — the model can read it — but it
   is better than nothing when (1) is unavailable.

State plainly in the product that this door is **weaker** than `/mneme:share`, and that a
contributor who wants the strong gate uses the CLI.

### 4.5 The dependency inversion, reduced
With the server in mneme's own process, what remains is one interface: `get_feedback_binding`'s
envelope. mneme depends on a Funes read verb; Funes depends on nothing of mneme's. That is the
right direction, and it is the opposite of v3's.

---

## 5. Roles

One row is true, and it is the one that matters:

| | Contributor | Committer |
|---|---|---|
| Repo role | fork-and-PR | **CODEOWNER**, curates |

Everything else v3's table claimed tracked something other than repo role. Both install mneme;
both consent; both use the same staging. The honest additional statement is about **gate
strength**, not trust: the committer's path is user-initiated, local, with the boundary gate
live; the contributor's is model-invocable with the compensating controls of §4.4. **The
contributor's door is the weaker one, which is the reverse of how v3's table read.**

You can use Funes without mneme. You cannot contribute without it — the trade the governance
buys.

---

## 6. The contributor is paid first

A contribution must not leave the contributor worse off. `Candidate.target` is a single string,
so routing *moves* a candidate.

**R1 — Contribute a copy; never relocate the original.** `candidate_id` hashes target *and*
body, so one body under two targets yields two candidates naturally — at ingest the adapter
simply emits two proposals. A *post-staging* copy needs a new verb, because `route` moves and
refuses to become a copy (*"this is a duplicate, not a move"*, `staging.py:257-262`).

**Two hazards R1 must handle, both verified:**
- **A decline on an *unassigned* candidate is recorded globally.** `_applies_to`
  (`staging.py:408-424`) returns `True` for every plugin when the ledger record's target is
  `""` or `unassigned` — as do all records written before the field existed. Declining one copy
  can silence the sentence for the scope repo too.
- **Applying one copy leaves its twin staged**, so the gate lists the same sentence twice until
  a human acts on both. That is R1's standing cost and it should be shown, not hidden.

**R2 — Never retire out of the contributor's own repo.** The `funes:` retirement applies to the
scope repo only.

**R3 — The scope repo is a mneme knowledge plugin.** Anyone with access installs it and
inherits the knowledge immediately, with no Oracle and no curator in the path. This also
resolves a hard mechanical constraint: `classify._require_destinations` refuses outright when
`units.maintains_skills(repo)` is false, so a plain repo cannot run `classify` and has no
`--retire` path. Plugin mode is required, and `mneme new` already scaffolds it.

---

## 7. The mapping

A mneme **unit** is a skill file *or one fact bullet* — `fact_unit_id(stem, bullet_text)`.

| mneme | Funes |
|---|---|
| topic file (`facts/<topic>.md`) | one **lesson** |
| fact bullet (= one unit) | one **learning** `{text, kind}` |
| authored at the gate | `summary` |
| authored at the gate, seeded from `#tags` | `concepts` |
| — | entities / facts — the curator's |

**Categories are suggested, never mapped.** `decision` and `gotcha` map exactly and may
default. `constraint`, `runbook-note` and `reference` are **confirmed by the contributor** — a
constraint is often contingent ("rate-limits at 100/s") where an invariant must always hold, and
a runbook note is site-specific where `how-to` implies general procedure. Filing contingent
facts as invariants teaches the graph that mutable things are permanent, in a store built with
`valid_from`/`valid_to` precisely to carry contingency.

**Provenance.** `lesson_key` = `mneme:<owner>/<repo>@<sha>#<unit-id>`, checkable against §4.3's
published pointer. `push_fact`'s required `evidence` is the bullet's own text.

---

## 8. Phase by phase

**Notice.** Committer: `mneme flag --kind funes-miss`. Contributor: `submit_fact`. Adding the
kind is free — nothing in `core/`, `bin/`, `hooks/` or `skills/` branches on a flag's kind. But
that is also why **"miss text stays local by default" does not exist yet**: kinds carry no
routing behaviour, so keeping miss text out of a shared repo is new work, not a label.

**Stage.** Read-only pre-validation via `validate_lesson_data`, `list_lesson_kinds`,
`get_lesson_kind`, `resolve_entity`. Two limits: it validates **shape, not sense** — `"This
also applies to the staging path."` clears every bound and means nothing outside its file — and
any readback a gate *relies* on must be attested, so `validate_lesson_data` at share and
`get_lesson` at retirement go through a first-party client while everything else is labelled
**unattested**. Schema bounds are **cached** from `get_lesson_kind` with a `fetched_at` stamp,
never hardcoded: `lint.lint_repo` takes a bare path with no per-repo config and blocks in three
places, so a Funes-derived rule there would fire on every mneme repo in existence.

**Gate.** Decontextualization is a rewrite and only the contributor has the context. Each bullet
must stand alone — an unresolved deictic (*this, above, the previous, also*) is flagged and
cannot ship. `summary` is **authored, not derived**: Funes wants a 12–1200 char paragraph and
mneme unit titles are short noun phrases. `concepts` are authored, seeded from tags, with
organizational tags (`#wip`, `#p2`) excluded by a denylist. Category confirmed. IP consent
recorded once — **before** the install, not after (§11).

**Share.** PR against the scope repo; mneme never writes main. Batched, deduplicated in-repo,
**fork-based** — and fork machinery **does not exist**: `push_branch` is `git push -u origin`,
`open_pr` passes no `--repo` and no fork head. A contributor without push access fails *after*
the gate. This is Phase-1 work and v3 did not budget it. `open_pr` must also **raise** instead
of returning the prose fallback it returns today when `gh` is missing or fails — a tool that
cannot distinguish "PR opened" from "PR not opened" cannot end a gate.

**Review.** A duplicate hint, advisory and non-blocking, consistent with Funes' own position
that restatements are shown and never collapsed.

A freshness gate on the contribution's own truth — but **not as v3 specified it**. `verified:`
is stamped at *ingest*, not capture (`cli.py:1135,1167`), so a gate on it measures time in the
PR queue, not the age of the knowledge, and a three-year-old fact submitted today reads as
verified today. The contributor's own capture date must travel as a distinct field for this
gate to mean anything. Credit where due: `mneme verify <plugin> --days N` already implements the
age arithmetic and exit-2 behaviour (`cli.py:683-758`); the work is lifting it out, not writing
it.

**Curate.** `/mneme:curate` — a **skill**, not a command; there is no `commands/` directory —
walks Funes' loop and retires the curated fact **in the scope repo only**, covered by
`funes:<scope_id>/<lesson_id>`. That covering reference is new machinery, not a flag:
`classify._accept_retirements` requires the covering id to exist **on the branch**, so it
threads through `_accept_retirements`, `_branch_unit_ids`, `_preservation_gate`,
`_parse_retirements` and the PR-body renderer. Per-fact outcomes, never per-run: multi-lesson
promotion is not atomic, so retire only facts whose lesson returned an id.

**Index.** mneme indexes the repo, never the graph.

---

## 9. Surfaces

**mneme side.** The binding lives in the **registry** — there is no `mneme.toml`, and
`scaffold.adopt` is forbidden by its own doctrine from rewriting repo content. New optional
fields on `registry.Plugin` (`funes_scope`, `funes_max_fact_age`,
`funes_tag_denylist = field(default_factory=list)`, `funes_repo_pointer`) — **but not
"backward-compatible by construction"**: `save_registry` silently drops unknown keys on every
write, so backlog item 4 is a prerequisite.

| Surface | Phase |
|---|---|
| **backlog: ingest hardening (caps, exit code, `--json`, registry round-trip)** | **0 — prerequisite** |
| `mneme-mcp` local stdio server: `submit_fact`, `confirm_submission`, `list_pending` | 1 |
| **fork-and-PR machinery**; `open_pr` raises instead of returning prose | 1 |
| registry binding; `--kind funes-miss`; candidate fan-out + copy verb | 1 |
| gate rewrite pass: deictics, authored summary/concepts, category | 1 |
| bootstrap: `init` → `new` → `registry add --clone` (**idempotent** — `mneme new` is not today) | 1 |
| MCP elicitation for `confirm_submission`; same-turn refusal | 2 |
| cached `get_lesson_kind`; attested client; capture-date field + freshness gate | 2 |
| `known-gaps.md`, `withdrawals.md`, upstream-rejection feedback | 2 |
| `/mneme:curate` skill; `funes:` covering references | 3 |

**Funes side:** §4.3's three items. That is all.

---

## 10. Decisions

| # | Decision | Chosen | Why |
|---|---|---|---|
| D0 | Channel | git pipeline + a submit front door | Rick/Omar |
| **D1** | **Where the verb lives** | **mneme's own local MCP server** | The read-only server runs on the Funes host behind forced SSH; a passthrough would exec in a shared home under a service identity |
| D2 | Why not write `funes_session_*` | it forks the pipeline; **and** there is no list API for the review step (`ARCHITECTURE.md:487`), the loop is write-server-only, and git supplies the review record | v3 said "skips the gate", which was a rationalization — Funes staging *could* have had a gate |
| D3 | Human gate for contributors | **MCP elicitation**, same-turn refusal, and an explicit statement that it is weaker than `/mneme:share` | An MCP tool cannot have `disable-model-invocation` |
| D4 | Install | **a user action outside the agent**: Funes returns a pointer and a pinned, copy-pasteable command | A server response causing an install one model-authored "yes" later is not consent |
| D5 | Repo kind | plugin mode + registry binding | `classify` refuses in a plain repo; plugin mode also pays the contributor at minute one |
| D6 | Category | two default, three confirmed at the gate | Two of v1's five mappings failed the test v1 used to reject the third |
| D7 | `valid_at` | curator states it, pre-filled with the capture date as a labelled lower bound | Null stores *ingest time*; `as_of` makes a mis-dated fact *absent* |
| D8 | Who builds the graph | curator; mneme contributes lessons and advises | mneme facts carry no identity |
| D9 | Decontextualization | the gate, by the contributor | Only they have the source; also the only throughput answer |
| D10 | Contribution semantics | copy (fan-out); never retire from the contributor's repo | Otherwise contributing leaves you worse off than doing nothing |
| D11 | What the PR carries | fact file + stable metadata only | A rendered payload rots on the first reviewer edit |

---

## 11. What still breaks

**Throughput — and there are three bottlenecks, not two.** Preview-confirm, CODEOWNER review,
and curation — the last held by the one person with the write server. mneme's own prior art
says review throughput kills knowledge commons (anthropics/skills: 762 open, unabsorbed), and
§4.2 deliberately lowers the barrier, so arrival goes **up**. Countermeasures: the contributor
produces the finished document (D9), batched PRs, in-repo dedup, `known-gaps.md` as
backpressure. **State a throughput budget before Phase 2** — contributions/week at minutes of
committer time. If it cannot be stated, the design has not been sized.

**Upstream rejection is invisible.** mneme *has* a rejection ledger — `declined.jsonl`, scoped
per target, checked at ingest — but it only records the contributor's own verdict. A PR the
scope repo closes teaches the contributor's mneme nothing, so the distiller re-proposes
forever. That is the missing piece, and a feedback path is its other half: without one there is
no reason to contribute twice.

**Escapes cannot be recalled.** The secret scan is a gate, not a proof. Here the path runs fact
file → PR → merge → Oracle → readable by everyone with the read-only server, and `git revert`
does not reach it. The only remedy is `retract_lesson`, which a contributor does not have.
`funes/withdrawals.md` with an SLA, actioned before the next promotion, is required.

**Consent and IP.** A contributor is putting knowledge from their own sessions — possibly their
employer's — into a store others read. The statement must come **before** the install, and it
must name what is actually involved: a CLI, two git repos, a GitHub token, and a public PR.
Note the commits say `mneme@localhost`, so the contributor is *not* named in git — which cuts
both ways and should be stated rather than discovered.

**Multi-scope contributors.** One binding, one scope. Per-scope bindings make this tractable;
routing between them is unsolved. Size before Phase 2.

**`list_scopes` cannot answer "does this scope exist?"** It caps at 100 and **raises `-20072`**
rather than truncating, and there is no `get_scope`.

---

## 12. For the Funes owner

1. **`feedback_repo_url` + `feedback_enabled` on `FUNES_SCOPES`** (or a side table),
   `get_feedback_binding` on both servers, `set_feedback_repo` on the write server. That is the
   whole ask — v3 wanted an exec tool and a doctrine exception; this wants three small
   operations and no behaviour outside PL/SQL.
2. **Confirm the deployment model** we read out of `_actor()` and the forced SSH command: is the
   read-only server intended to run on the Funes host with one key per client? The entire
   design turns on it.
3. **Would you add a `reference` learning kind?** And does `AGENT_VOCABULARY_POLICY`
   (`sql/43_agent_learning.sql:202`) permit a curator acting on someone else's contribution to
   create kinds in-loop?
4. **`schemas/operations_contract_v1.md` and `schemas/README.md` present a removed subsystem as
   authoritative** — worth a staleness banner like `ARCHITECTURE.md`'s. Separately,
   `list_scopes` (§11).

---

## 13. Delivery

**Phase 0 — prerequisites.** `backlog/2026-09-11-ingest-hardening.md`: cap `topic`/`name`/tags,
non-zero exit when everything is rejected, a `--json` result carrying ids and rendered bullets,
`--source` sanitised, registry round-trip preserved. None of the rest can be built on the door
as it stands.

**Phase 1 (build, no Funes access).** `mneme-mcp`; fork-and-PR; `open_pr` raising; registry
binding; fan-out and the copy verb; the gate rewrite pass; idempotent bootstrap. Fully testable
with recorded envelopes.

**Phase 2 (release with 1).** Elicitation; attested client; cached `get_lesson_kind`; capture
date + freshness gate; `known-gaps.md`, `withdrawals.md`, upstream-rejection feedback; required
CI on the scope repo. **Funes side:** §12.1.

**Phase 3.** `/mneme:curate`; `funes:` covering references; per-fact outcomes.

Build Phase 1 first, **release it with Phase 2** — shipping capture alone produces the
accumulating queue mneme's own prior art names as the failure mode. Ship the drain before the
source. Phase 3 needs a live scope, and a fault-injected one for the partial-promotion path;
that is the only place the plan is blocked on access rather than work.

---

## 14. What would prove this works

1. **A contributor gets an answer they would not otherwise have had** — the fact is in their own
   repo and retrieval path from the moment they approve it, before any PR is reviewed and with
   Funes nowhere in the loop.
2. A second person installs the scope repo as a plugin and inherits the knowledge before any
   curation has happened.
3. A contributor submits through `mneme-mcp` **running on their own machine**, against their own
   `MNEME_HOME` and git identity — never the Funes host's.
4. `confirm_submission` cannot complete without a protocol-level user response; in the same
   assistant turn as its `submit_fact`, it is refused.
5. A bullet with an unresolved deictic cannot ship, and what does ship reads correctly with its
   file removed.
6. A restricted-repo source produces a boundary warning toward a widely-read scope **on the
   submit path**, not only the CLI path.
7. A validation verdict is either attested or visibly labelled unattested.
8. A contributor with no push access gets a fork-based PR, and a failure to open it is an error
   rather than a sentence.
9. `/mneme:curate` promotes a merged contribution; `get_lesson` returns it with the contributing
   commit in `lesson_key`; the fact is retired **in the scope repo only** and still present in
   the contributor's.
10. A curate run interrupted after its first `promote_lesson` is re-runnable.
11. A PR closed upstream stops the distiller re-proposing it, and its author is told why.
12. A withdrawal is actioned before the next promotion.
