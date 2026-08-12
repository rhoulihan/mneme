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
        "skills/knowledge-index/SKILL.md": templates.render(templates.INDEX_SKILL_MD, **subs),
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
        "skills/knowledge-index/SKILL.md": templates.render(templates.INDEX_SKILL_MD, **subs),
    }
    # Adoption seeds the canonical facts location — unless this repo already files facts
    # at the top level, which stays readable (both layouts resolve via `units.facts_dir`)
    # and must not be shadowed by an empty canonical directory.
    if not (target / "facts").is_dir():
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


def regenerate_index_skill(target: Path, name: str, description: str) -> Path:
    facts = units.facts_dir(target)
    entries: list[tuple[str, str, int]] = []
    if facts.is_dir():
        for f in sorted(facts.glob("*.md")):
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
    text = templates.render(
        templates.INDEX_SKILL_MD, name=name, description=" ".join(description.split()),
        owner="", sensitivity="",
    )
    meta, body = units.parse_frontmatter(text)
    rendered = str(meta["description"])
    if entries:
        topic_list = ", ".join(t for t, _, _ in entries)
        rendered += f" Topics: {topic_list}"
    # Cap on every path — the template boilerplate alone can push a lint-clean caller
    # description over the limit, topics or not.
    meta["description"] = rendered[: lint.MAX_DESCRIPTION]
    rows = "".join(f"| {t} | {p} | {c} |\n" for t, p, c in entries)
    out = units.serialize_frontmatter(meta, body + rows)
    path = target / "skills" / "knowledge-index" / "SKILL.md"
    path.write_text(out, encoding="utf-8")
    return path
