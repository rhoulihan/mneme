"""Knowledge-plugin scaffold templates (spec §5.1, §8). Pure data — no logic."""
from __future__ import annotations

import json
from string import Template


def render(template: str, **subs: str) -> str:
    return Template(template).substitute(**subs)


def render_json(template: str, **subs: str) -> str:
    """Render a JSON template, JSON-escaping every substitution value.

    The JSON templates embed placeholders *inside* string literals (`"$description"`),
    so raw user text containing a double quote, backslash, or control character would
    break the manifest. Escaping each value as a JSON string body (quotes stripped)
    keeps the rendered document `json.loads`-clean for any input.
    """
    escaped = {k: json.dumps(str(v), ensure_ascii=False)[1:-1] for k, v in subs.items()}
    return render(template, **escaped)


PLUGIN_JSON = """{
  "name": "$name",
  "version": "0.1.0",
  "description": "$description"
}
"""

MARKETPLACE_JSON = """{
  "name": "$name",
  "owner": { "name": "$owner" },
  "plugins": [
    { "name": "$name", "source": "./", "description": "$description" }
  ]
}
"""

MNEME_MD = """# $name — knowledge scope

**Sensitivity:** $sensitivity
**Contribution mode:** $mode
**Maintainers:** $owner

## Scope statement

$description

## What belongs here

- Hard-won procedures (skills): verified fixes, deployment paths, debugging golden paths — each with the failure pattern that made it non-obvious.
- Durable facts: constraints, gotchas, decisions, runbook notes that stay true across tickets.

## What does NOT belong here

- One-off decisions tied to a single ticket or conversation.
- Secrets, credentials, tokens, or personal data — the capture pipeline blocks them, and so does CI.
- Anything derivable from public documentation.

## Routing

This scope statement is the routing prompt: mneme's distiller matches candidate knowledge
against it. Keep it specific — name the products, systems, and processes this plugin covers.
"""

AGENTS_MD = """# $name

Agent-facing knowledge plugin. Procedural knowledge lives in `skills/` (Agent Skills
format); durable facts live in `facts/` and are routed through the `knowledge-index`
skill. See `MNEME.md` for what belongs here and `CONTRIBUTING.md` for how knowledge
gets in.
"""

README_MD = """# $name

$description

A [mneme](https://github.com/rhoulihan/mneme) knowledge plugin: procedures as Agent
Skills in `skills/`, durable facts in `facts/`, governance in CI. Install it through
your agent's plugin marketplace tooling and inherit every merged update.

- Scope and routing: `MNEME.md`
- Contribution pipeline: `CONTRIBUTING.md`
- Reviewers: `CODEOWNERS`
"""

CONTRIBUTING_MD = """# Contributing knowledge to $name

Knowledge enters this repo through pull requests — human-written or staged by mneme's
curated harvest. Either way the same rules apply.

## The promotion rule

A contribution must carry:

1. **Verified success** — the procedure or fact was actually exercised, not assumed.
2. **A named failure pattern** — what went wrong before the fix; the dead ends eliminated.
3. **Non-obviousness** — not derivable from public documentation.

## Format

- Skills: `skills/<name>/SKILL.md`, kebab-case `name` matching the directory,
  trigger-rich `description` (it IS the retrieval surface), provenance in `metadata`.
- Facts: one topic per file in `facts/`, typed bullets
  (`decision | constraint | gotcha | runbook-note | reference`), tags, verified dates.
- Delta edits only — never regenerate whole files.

CI (`validate.yml`) lints format and scans for secrets, so review can focus on substance.

## Review policy

- CODEOWNERS routes each area to its maintainers.
- Unreviewed AI-generated bulk contributions are closed without merge; every PR needs a
  human who vouches for the promotion rule above.
- Merges bump the plugin version automatically — accepted knowledge ships immediately.
"""

CODEOWNERS = """# Default reviewers for all knowledge in this plugin.
# Add per-area rules above the fallback as the repo grows, e.g.:
#   /skills/deploy-*  @platform-team
* @$owner
"""

VALIDATE_YML = """name: validate
on:
  pull_request:
  push:
    branches: [main]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Fetch mneme engine
        run: git clone --depth 1 https://github.com/rhoulihan/mneme /tmp/mneme
      - name: Lint knowledge units
        run: /tmp/mneme/bin/mneme lint .
      - name: Secret scan
        run: |
          set -e
          rc=0
          while IFS= read -r -d '' f; do
            /tmp/mneme/bin/mneme scan "$$f" || rc=$$?
          done < <(find skills facts -name '*.md' -print0 2>/dev/null)
          exit $$rc
"""

RELEASE_YML = """name: release
on:
  push:
    branches: [main]
jobs:
  bump:
    if: "!contains(github.event.head_commit.message, 'chore: bump version')"
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - name: Bump plugin version
        run: |
          python3 - <<'PY'
          import json, pathlib
          p = pathlib.Path('.claude-plugin/plugin.json')
          data = json.loads(p.read_text())
          major, minor, patch = data['version'].split('.')
          data['version'] = f"{major}.{minor}.{int(patch) + 1}"
          p.write_text(json.dumps(data, indent=2) + "\\n")
          PY
      - name: Commit bump
        run: |
          git config user.name "mneme-bot"
          git config user.email "mneme-bot@users.noreply.github.com"
          git commit -am "chore: bump version"
          git push
"""

GITIGNORE = """.DS_Store
Thumbs.db
__pycache__/
"""

INDEX_SKILL_MD = """---
name: knowledge-index
description: Consult when you need durable facts from $name — constraints, gotchas, decisions, and runbook notes. $description Topics listed in this skill route to fact files under facts/.
---

# $name fact index

Regenerated mechanically by mneme — do not edit by hand.

| Topic | File | Bullets |
|---|---|---|
"""

NOTICING_BRIEF = """## mneme noticing

While you work, flag knowledge worth keeping — do NOT stop to document it.

Flag (one line each, at the moment it happens) when:
- a hard-won fix lands after real dead ends: `mneme flag "<what worked + why it was non-obvious>"`
- installed knowledge proves wrong or stale: `mneme flag --kind knowledge-issue "<what is wrong>"`

Rules: one line per flag; no mid-session distillation (a background distiller runs later);
never flag anything from excluded repos/paths; never include secrets or credentials in flag text.
"""

DISTILLER_PROMPT = """You are the mneme DISTILLER — a separate curation role, not the working agent.
Read the session evidence and extract ONLY knowledge that clears the promotion rule:
1. Verified success — it actually worked in this session, not assumed.
2. A named failure pattern — what went wrong before the fix; dead ends eliminated.
3. Non-obvious — not derivable from public documentation.

Session flags (the working agent marked these moments):
$flags

Transcript: $transcript_path

Registered knowledge plugins (route each proposal to the best-matching scope;
use "unassigned" when no scope clearly fits — never guess across scopes):
$scopes

Before proposing, check what already exists: you may run `bin/mneme search "<query>"`
and `bin/mneme db query "SELECT ..."`. When existing knowledge covers the same ground,
emit an "update" edit against that unit id instead of a near-duplicate "new".

Output EXACTLY one JSON object, no prose, matching:
{
  "proposals": [
    {
      "type": "skill", "edit": "new" | "update", "target": "<plugin-name>" | "unassigned",
      "target_unit": "<unit id, required when edit=update>",
      "name": "<kebab-case-skill-name>", "description": "<trigger-rich, <=1024 chars>",
      "procedure": "<verified steps, markdown>", "failure_pattern": "<what failed first, markdown>",
      "confidence": 0.0, "rationale": "<why this clears the promotion rule>"
    },
    {
      "type": "fact", "edit": "new" | "update", "target": "<plugin-name>" | "unassigned",
      "target_unit": "<unit id, required when edit=update>",
      "topic": "<kebab-case-topic>", "category": "decision|constraint|gotcha|runbook-note|reference",
      "text": "<single factual statement>", "tags": ["<tag>"],
      "confidence": 0.0, "rationale": "<why this clears the promotion rule>"
    }
  ]
}

Emit an empty proposals array when nothing clears the rule — silence beats noise.
Never include secrets, tokens, passwords, or personal data in any field.
"""
