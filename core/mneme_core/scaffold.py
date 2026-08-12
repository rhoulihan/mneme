"""Scaffold factory — generates governed knowledge-plugin repos (spec §5.1, §8)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import lint, paths, registry, templates
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
    mode: str = "pr",
    sensitivity: str = "internal",
) -> Path:
    if not KEBAB_RE.match(name):
        raise MnemeError(f"plugin name must be kebab-case: {name!r}")
    target = directory if directory is not None else paths.repos_dir(home) / name
    if target.exists():
        raise MnemeError(f"target already exists: {target}")
    if not description:
        description = f"Institutional knowledge maintained with mneme: {name}."
    subs = dict(
        name=name, description=description, owner=owner, sensitivity=sensitivity, mode=mode
    )

    files = {
        ".claude-plugin/plugin.json": templates.render(templates.PLUGIN_JSON, **subs),
        ".claude-plugin/marketplace.json": templates.render(templates.MARKETPLACE_JSON, **subs),
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
    for rel, content in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (target / "facts").mkdir(exist_ok=True)

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
            mode=mode,
            sensitivity=sensitivity,
        ),
    )
    return target
