"""Scaffold factory — generates governed knowledge-plugin repos (spec §5.1, §8)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import lint, paths, registry, templates, units
from .errors import MnemeError
from .units import KEBAB_RE


def _git(target: Path, *args: str) -> None:
    cmd = [
        "git",
        "-c", "user.name=mneme",
        "-c", "user.email=mneme@localhost",
        "-C", str(target),
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise MnemeError(f"git {' '.join(args)} failed: {result.stderr.strip()[:300]}")


def create(
    home: Path,
    name: str,
    *,
    directory: Path | None = None,
    description: str = "",
    owner: str = "maintainers",
    repo_url: str = "",
    sensitivity: str = "internal",
) -> Path:
    if not KEBAB_RE.match(name):
        raise MnemeError(f"plugin name must be kebab-case: {name!r}")
    target = directory if directory is not None else paths.repos_dir(home) / name
    if target.exists():
        raise MnemeError(f"target already exists: {target}")
    if not description:
        description = f"Institutional knowledge maintained with mneme: {name}."
    subs = dict(name=name, description=description, owner=owner, sensitivity=sensitivity)

    files = {
        ".claude-plugin/plugin.json": templates.render_json(templates.PLUGIN_JSON, **subs),
        ".claude-plugin/marketplace.json": templates.render_json(
            templates.MARKETPLACE_JSON, **subs
        ),
        "MNEME.md": templates.render(templates.MNEME_MD, **subs),
        "AGENTS.md": templates.render(templates.AGENTS_MD, **subs),
        "README.md": templates.render(templates.README_MD, **subs),
        "CONTRIBUTING.md": templates.render(templates.CONTRIBUTING_MD, **subs),
        "CODEOWNERS": templates.render(templates.CODEOWNERS, **subs),
        ".github/workflows/validate.yml": templates.VALIDATE_YML,
        ".github/workflows/release.yml": templates.RELEASE_YML,
        ".gitignore": templates.GITIGNORE,
        "skills/knowledge-index/SKILL.md": templates.render(
            templates.INDEX_SKILL_MD, index_name="knowledge-index", **subs
        ),
    }
    # Manifests are the one machine-parsed artifact lint_repo never sees; verify them
    # here so a broken manifest can never reach disk, the first commit, or the registry.
    for rel in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"):
        try:
            json.loads(files[rel])
        except json.JSONDecodeError as e:
            raise MnemeError(f"scaffold generated invalid JSON in {rel} (bug): {e}") from e

    for rel, content in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    facts = target / units.FACTS_CANONICAL
    facts.mkdir(parents=True, exist_ok=True)
    (facts / ".gitkeep").write_text("", encoding="utf-8")
    regenerate_index_skill(target, name, description)

    issues = lint.lint_repo(target)
    if lint.has_errors(issues):
        details = "; ".join(f"{i.code} {i.message}" for i in issues if i.severity == "error")
        raise MnemeError(f"scaffold generated a repo that fails lint (bug): {details}")

    _git(target, "init", "-b", "main")
    _git(target, "add", "-A")
    _git(target, "commit", "-m", f"chore: scaffold {name} knowledge plugin")

    registry.add_plugin(
        home,
        registry.Plugin(
            name=name,
            repo=repo_url or f"local:{target}",
            path=str(target),
            sensitivity=sensitivity,
        ),
    )
    return target


def adopt(
    home: Path, name: str, *, description: str = "", owner: str = "maintainers"
) -> list[str]:
    plugin = registry.get_plugin(home, name)
    if plugin is None:
        raise MnemeError(f"plugin not registered: {name}")
    target = Path(plugin.path)
    if not target.is_dir():
        raise MnemeError(f"local clone missing: {target}")
    if not description:
        description = f"Institutional knowledge maintained with mneme: {name}."
    subs = dict(
        name=name, description=description, owner=owner,
        sensitivity=plugin.sensitivity,
    )
    candidates = {
        "MNEME.md": templates.render(templates.MNEME_MD, **subs),
        "CONTRIBUTING.md": templates.render(templates.CONTRIBUTING_MD, **subs),
        "CODEOWNERS": templates.render(templates.CODEOWNERS, **subs),
        ".github/workflows/validate.yml": templates.VALIDATE_YML,
        ".github/workflows/release.yml": templates.RELEASE_YML,
        ".claude-plugin/plugin.json": templates.render_json(templates.PLUGIN_JSON, **subs),
        ".claude-plugin/marketplace.json": templates.render_json(
            templates.MARKETPLACE_JSON, **subs
        ),
        "skills/knowledge-index/SKILL.md": templates.render(
            templates.INDEX_SKILL_MD, index_name="knowledge-index", **subs
        ),
    }
    # Adoption seeds the canonical facts location, unconditionally — including in a repo
    # that still files facts at the top level. Skipping it there (Plan 10) left the adopted
    # repo with nowhere canonical to write, so its very next fact re-confirmed the legacy
    # layout: accommodation is what keeps a pre-0.5 repo legacy forever. The seeded
    # directory is empty, so it shadows nothing — every reader sweeps both layouts
    # (`units.fact_files`) until `layout.migrate_legacy_facts` runs on the next
    # contribution. What adopt must not do is any of that migrating itself: it adds files
    # that are missing and never rewrites, moves or deletes repo content.
    candidates[f"{units.FACTS_CANONICAL}/.gitkeep"] = ""
    added: list[str] = []
    for rel, content in candidates.items():
        path = target / rel
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        added.append(rel)
    if "skills/knowledge-index/SKILL.md" in added:
        regenerate_index_skill(target, name, description)
    return added


def _topic_tail(count: int) -> str:
    """What the description says about the facts — a COUNT, never the list of names.

    The list used to be spelled out here, one entry per fact file, which makes the
    description O(n) in the size of the repo: any budget is a cliff the repo walks off as
    it grows, and `MAX_DESCRIPTION` is a hard platform limit rather than a preference. A
    count is O(1), so this holds at three topics and at three hundred.

    It also carries the routing hint that used to be a fixed sentence in the template.
    Saying "topics are listed in this skill" twice cost ~60 characters of a 500-character
    budget, and those characters come out of the author's own scope statement — the one
    part of the description that is not boilerplate.

    Nothing is lost by it. This description is what an agent reads to decide *whether* to
    open the skill; the topic names it needs *after* deciding are in the body table, which
    costs nothing until the skill is opened. (Facts also migrate into their related skills
    over time via `/mneme:classify`, so the index is a waypoint, not a permanent home.)
    """
    if count == 0:
        return " No facts recorded yet."
    return f" {count} topic{'s' if count != 1 else ''}, listed in this skill, stored in facts/."


def _fit(text: str, budget: int) -> str:
    """`text` trimmed to `budget` characters, never mid-word.

    The old cap was a bare slice, which could sever the final token and leave a
    half-written topic name that routes nowhere — and said nothing about having dropped
    anything, so the description read as complete. An ellipsis costs one character and
    tells the reader the sentence was cut.
    """
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    cut = text[: budget - 1]
    head, sep, _tail = cut.rpartition(" ")
    return (head if sep else cut) + "…"


def regenerate_index_skill(target: Path, name: str, description: str) -> Path:
    # Where the router goes is the repo's mode, not a constant: a plugin's belongs in
    # `skills/knowledge-index/` where Claude Code discovers it, a plain repo's in
    # `mneme-index/` where it collides with nothing the application owns. The skill is
    # named for the directory it lands in because MN003 requires exactly that.
    root = units.knowledge_root(target)
    index_name = root.name
    entries: list[tuple[str, str, int]] = []
    # Every fact file, in both layouts: the index skill IS the routing surface, so a topic
    # missing from this table is a topic no agent is told exists.
    for f in units.fact_files(target):
        topic = f.stem
        count = 0
        try:
            meta, body = units.parse_frontmatter(f.read_text(encoding="utf-8-sig"))
            topic = str(meta.get("topic", f.stem))
            for n, line in enumerate(body.splitlines(), start=1):
                if line.startswith("- ["):
                    try:
                        units.parse_bullet_line(line, n)
                        count += 1
                    except MnemeError:
                        continue
        except MnemeError:
            pass
        # `facts/<file>` in both layouts: relative to this skill's own directory in
        # the canonical layout, relative to the repo root in a legacy one — and it is
        # also the prefix of the unit id, which never moves with the files.
        entries.append((topic, f"facts/{f.name}", count))

    # The description lands on a single frontmatter line: fold any newline/tab the
    # caller supplied so the rendered template stays parseable before we cap it.
    scope = " ".join(description.split())
    tail = _topic_tail(len(entries))
    # Measured, not estimated: the boilerplate varies with the plugin name, so the room
    # left for the caller's scope statement is whatever the template does not already use.
    empty = templates.render(
        templates.INDEX_SKILL_MD, name=name, index_name=index_name,
        description="", owner="", sensitivity="",
    )
    fixed = len(str(units.parse_frontmatter(empty)[0]["description"])) + len(tail)
    text = templates.render(
        templates.INDEX_SKILL_MD, name=name, index_name=index_name,
        description=_fit(scope, units.MAX_DESCRIPTION - fixed),
        owner="", sensitivity="",
    )
    meta, body = units.parse_frontmatter(text)
    # A final clamp, because a pathological plugin name can consume the budget on its own
    # and there is then nothing left to trim — better a short description than an invalid one.
    meta["description"] = (str(meta["description"]) + tail)[: units.MAX_DESCRIPTION]
    rows = "".join(f"| {t} | {p} | {c} |\n" for t, p, c in entries)
    out = units.serialize_frontmatter(meta, body + rows)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "SKILL.md"
    path.write_text(out, encoding="utf-8")
    return path
