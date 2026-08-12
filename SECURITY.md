# Security policy

Mneme sits between an AI agent and a shared git repository, and its entire value is that
nothing crosses that boundary without passing a deterministic machine gate and an
explicit human approval. A defect that lets content cross either gate unnoticed is a
security defect, not a bug.

## Reporting a vulnerability

Report privately through **GitHub security advisories**:
[github.com/rhoulihan/mneme/security/advisories/new](https://github.com/rhoulihan/mneme/security/advisories/new)
(repo → *Security* → *Report a vulnerability*). Please do not open a public issue, PR, or
discussion for a suspected vulnerability.

Include: the version or commit, the exact command or hook payload, what the gate did,
and what it should have done. A minimal reproduction — ideally a failing pytest case
against a `tmp_path` home — is the fastest possible path to a fix.

What to expect: acknowledgement within 3 business days, an assessment with a severity
call within 10, and a fix released for anything confirmed in-scope before the advisory is
published. This is a solo-maintained project with no paid bounty; credit in the advisory
is offered unless you'd rather stay anonymous. Coordinated disclosure is the default —
please give a fix a chance to ship before going public.

## In scope

The gates and the trust boundaries around them:

- **Machine-gate bypasses.** Anything that gets a candidate into staging, or into a
  harvest commit, without clearing the promotion rule, schema validation, size caps, or
  canonical rendering.
- **Secret-scan evasion.** Credential-bearing content that reaches staging unquarantined
  or lands in a commit — encodings, line splits, unit fields the scanner never sees.
- **Declined-ledger and dedup evasion.** Content the user already declined resurfacing as
  a fresh candidate, or duplicate suppression being defeated by cosmetic changes
  (hashing is on semantic content precisely so stamps and dates cannot reset it).
- **`mneme db query` read-only escapes.** Any input that writes, attaches, detaches,
  changes pragmas, or reads a file outside the opened index despite the SELECT-only
  guard and the read-only URI connection.
- **Hook-context injection.** Content from a repo, transcript, or knowledge unit that
  reaches the agent's injected context as instructions rather than data, via the
  SessionStart brief or any other hook output.
- **Scaffold template injection.** Values passed to `mneme new` / `mneme adopt` (names,
  descriptions, owners, URLs) that break out of the generated manifests, workflows, or
  markdown into executable or structurally different output.
- **Path traversal and unintended writes.** Any code path that writes outside the mneme
  home or the target knowledge repo, or that follows attacker-chosen paths out of them.
- **Provenance forgery.** Harvest commits, trailers, or submission records that
  misattribute authorship, source, or approval.

## Out of scope

- Anything requiring an attacker who already has write access to your machine, your
  `~/.mneme`, or your knowledge repo — mneme trusts the local user by design.
- The quality or truthfulness of knowledge a human approved. The human gate is the
  control; bad knowledge that a reviewer merged is a review failure, not a vulnerability.
- Vulnerabilities in git, Python, SQLite, GitHub, or the agent harness themselves.
  Report those upstream; if mneme *uses* them unsafely, that part is in scope.
- Missing hardening with no demonstrated impact. Show the bypass.
- Denial of service by feeding mneme absurd local input. Hook scripts are required to
  exit 0 on every path and degrade quietly; a hook that merely does nothing is behaving
  as designed.

## Design notes for reviewers

Useful context when you go looking: there is no network egress in the engine except the
git operations you configure, and no auto-push mode — harvests stop at a branch or a
pull request. LLM output is never trusted: proposals are validated, rendered by tested
code, scanned, and staged. The index database is derived and disposable; the markdown
files in git are canonical. Sensitivity labels (`public | internal | restricted`) bound
routing, so a candidate silently landing in a less-restricted target is in scope too.
