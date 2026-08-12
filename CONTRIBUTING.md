# Contributing to mneme

This is the **engine** repo: the CLI, the retrieval index, the machine gate, the harness
adapter. Knowledge itself does not live here — see [Knowledge belongs elsewhere](#knowledge-belongs-elsewhere)
below.

Mneme is built spec-first and plan-driven, with strict TDD. That process is not
ceremony: it is the same discipline mneme exists to enforce on knowledge — machines
settle format, humans gate substance. Contributions are welcome when they arrive
through it.

## The process

1. **The spec is authoritative.** [`docs/superpowers/specs/`](docs/superpowers/specs/)
   holds the design specification. If a change contradicts the spec, the spec changes
   first — in its own PR, with the reasoning — and only then does code follow. "The code
   does X" is never an argument against the spec; it is a bug report.
2. **Changes land through implementation plans.** Anything beyond a typo or a one-line
   fix gets a plan in [`docs/superpowers/plans/`](docs/superpowers/plans/), following the
   shape of the existing ones: numbered tasks, each carrying its full test code *and* its
   implementation code, each independently committable. Writing the plan is where the
   design argument happens; review it before writing product code.
3. **Tasks are test-first, without exception.** Write the failing test, run it, confirm
   it fails for the reason you expect, then implement. Never weaken, skip, or delete a
   test to get a green run — a failing test is a finding, and the fix goes in the product
   code.
4. **One commit per task**, message in the plan's imperative style (`feat:`, `fix:`,
   `test:`, `docs:`, `ci:`, `release:`).

## Every PR must be green

Two commands, run from the repo root, both exit 0 — locally before you push, and again
in CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml), on Python 3.10 and 3.12):

```bash
python3 -m pytest       # the full suite, not just the module you touched
bin/mneme lint .        # the engine's own skills, through the engine's own linter
```

The self-lint is deliberate dogfood: every `SKILL.md` shipped in this repo has to satisfy
the schema mneme imposes on everyone else's knowledge. If the linter rejects something
you wrote, fix the skill — not the linter.

There are no runtime dependencies and there will not be any: the engine is
standard-library-only, Python ≥ 3.10, plus `git` on the machine. `pytest` is the only
dev-time requirement (CI also installs `pyyaml`, which a single frontmatter test uses via
`importorskip` and skips cleanly without). A PR that adds an import outside the standard
library needs to argue its case in the spec first.

## Test layout

One test module per source module, mirroring the tree:

```
tests/core/      # engine modules (core/mneme_core/)
tests/index/     # retrieval component (core/mneme_index/)
tests/adapter/   # hooks, commands, plugin manifests
tests/e2e/       # whole-loop tests through the real binaries and hook scripts
```

Tests write only to pytest `tmp_path` directories. Nothing may touch the real
`~/.mneme`, the network, or a real `claude` binary — e2e tests use scratch homes, a
shimmed `claude`, and local bare git remotes. A test that leaks state outside a tmp dir
is a broken test.

## Rules the code has to keep

- **Hook scripts exit 0 on every path.** A hook that fails a session is worse than a hook
  that does nothing. Guard, swallow, exit 0.
- **The LLM never edits the store.** Model output enters as untrusted structured
  proposals; tested code validates, renders, scans, dedups, and stages. If your change
  lets a prompt write a file directly, it is the wrong change.
- **Delta edits only.** Units are individually addressable; nothing regenerates a whole
  knowledge file.
- **Files are canonical, the database is derived.** Any index must be rebuildable from
  the markdown at any time.

## Knowledge belongs elsewhere

Skills and facts about *using* a product, a codebase, or a process do not belong in this
repo. They belong in a knowledge plugin — `bin/mneme new your-knowledge` scaffolds one,
governance included. Mneme's own build lessons live in
[`docs/dogfood/seed-proposals.json`](docs/dogfood/seed-proposals.json), which seeds the
public `mneme-dev-knowledge` plugin; add to that file, in the distiller's proposal
schema, rather than adding prose here. The engine repo ships engine code.

## Reporting bugs and vulnerabilities

Ordinary bugs: open an issue with the command you ran, what you expected, and what
happened — a failing test case is worth ten paragraphs. Security issues go through the
private channel in [SECURITY.md](SECURITY.md), never a public issue.
