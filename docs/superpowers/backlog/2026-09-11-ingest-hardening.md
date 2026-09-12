# Backlog: `distill ingest` is the untrusted door and three of its locks are open

**Raised** 2026-09-11 by the adversarial review of the Funes integration spec
(`docs/superpowers/specs/2026-09-11-funes-knowledge-repo.md`). All four REPRODUCED against
v0.9.0. None is caused by the Funes work — they are live defects on the shipped path — but
that spec turns `distill ingest` into a door a *general user* can knock on, which is why
they surfaced now and why they should land before any of it.

`proposals.py:13-15` states the doctrine these violate:

> Caps on untrusted fields: proposals arrive as LLM output, so every unbounded string is a
> memory/ledger-bloat vector. Sizes are generous enough that honest content never trips them.

## 1. Three fields are uncapped, and one proposal can write a 300 KB candidate (HIGH)

`topic` is checked against `KEBAB_RE` only (`proposals.py:107-108`), `name` against a kebab
check with no `_cap` (`:87-88`), and each individual **tag** against
`_TAG_RE.fullmatch(r"[\w-]+")` with no length bound (`:114`) — only the tag *count* is capped
at 20. Every other untrusted string is capped.

Reproduced: one proposal with a 100 000-char `topic` and a 200 000-char tag staged cleanly,
exit 0, and wrote a **300 327-byte** candidate file. The comment above says this is exactly
what the caps exist to prevent.

`topic` is also the destination *filename*, so an unbounded value is a path-length problem on
top of a bloat one.

- [ ] `_cap` on `topic`, `name`, and each tag. Sizes in the spirit of the existing ones —
      generous enough that honest content never trips them.

## 2. Every proposal can be rejected and ingest still exits 0 (HIGH)

`cli.py:1241` returns 0 unconditionally. Only a document-level failure — unreadable path,
malformed JSON — raises and exits 1. Reproduced: a document whose single proposal was
rejected printed `staged 0 … rejected 1` and **exited 0**.

Today the only caller is the distiller pipeline, which nobody watches, so a run that threw
everything away is indistinguishable from a quiet one. Any machine caller — which the Funes
spec proposes — reads success.

- [ ] Non-zero exit when `staged == 0 and rejected > 0`, and a distinct code from the
      malformed-document case so a caller can tell "I sent junk" from "I sent nothing usable".
- [ ] While here: emit a machine-readable result (`--json`) carrying per-candidate id, rendered
      bullet, findings and per-proposal rejection reasons. Today none of that is printed —
      `cli.py:1187` computes scan findings and discards all but `status` — so a caller cannot
      learn what was staged without recomputing `candidate_id` itself.

## 3. `--source` is interpolated unsanitised into a commit trailer (MED)

`gitops.py:145`: `"\n".join(f"Mneme-Source: {s}" for s in sorted(set(sources)))`. `--source`
is uncapped and unvalidated (`cli.py:194`) and flows to this line. A newline in it forges
arbitrary trailers in the harvest commit.

The frontmatter path is safe — values are JSON-quoted, verified by round-tripping an embedded
`\nstatus: staged\nid: pwned` — so this is the commit message alone.

- [ ] Reject or escape control characters in `--source` at the CLI boundary, where the
      untrusted value enters, rather than at the one site that happens to interpolate it.

## 4. `save_registry` silently drops fields it does not know (MED, latent)

`load_registry` filters entries to `Plugin.__dataclass_fields__` (`registry.py:39-43`) — so
an older binary *reads* a newer registry without error. But `save_registry` rewrites the whole
file from `asdict()` over those filtered objects (`:50-53`), so any write by a binary lacking
a field **permanently drops it for every plugin**, with no warning.

Reproduced: a hand-added `funes_scope` / `funes_max_fact_age` on one entry, then
`mneme registry add third --repo …` on the 0.9.0 binary — both fields gone from the rewritten
file, silently.

Harmless today because no such field exists. It stops being harmless the moment one does, and
it fails **open**: a dropped sensitivity binding is not a refusal, it is a missing check. This
is the finding that makes "new registry fields are additive and backward-compatible by
construction" false, and it belongs here rather than in the spec that assumed it.

- [ ] Preserve unknown keys across a load/save round-trip, or refuse to save a registry whose
      on-disk version is newer than the binary's.

## Verification

1. A 100 000-char topic and a 200 000-char tag are both rejected, and the candidate file for
   an honest proposal is unchanged in size.
2. A document whose every proposal is rejected exits non-zero, with a different code from a
   malformed document.
3. A `--source` containing a newline is refused at the CLI, and no harvest commit can carry a
   forged trailer.
4. An unknown registry key survives `registry add` by an older binary — or the save is refused.
