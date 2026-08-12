"""Applying approved candidates to knowledge-repo clones (spec §7.3)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import gitops, lint, paths, registry, scan, units
from . import scaffold as scaffold_mod
from .errors import MnemeError
from .staging import Candidate


def _skill_name(cand: Candidate) -> str:
    meta, _body = units.parse_frontmatter(cand.body)
    name = str(meta.get("name", ""))
    if not name:
        raise MnemeError(f"candidate {cand.id}: skill body has no frontmatter name")
    return name


def apply_skill(repo: Path, cand: Candidate) -> str:
    name = _skill_name(cand)
    skill_md = repo / "skills" / name / "SKILL.md"
    if cand.edit == "new":
        if skill_md.exists():
            raise MnemeError(
                f"candidate {cand.id}: skills/{name} already exists — expected an update edit"
            )
        skill_md.parent.mkdir(parents=True, exist_ok=True)
        skill_md.write_text(cand.body, encoding="utf-8")
        return f"skills/{name} (new skill)"
    expected = cand.target_unit.removeprefix("skills/")
    if name != expected:
        raise MnemeError(
            f"candidate {cand.id}: body names skill {name!r} but targets {cand.target_unit!r}"
        )
    if not skill_md.exists():
        raise MnemeError(f"candidate {cand.id}: update target {cand.target_unit} not found")
    skill_md.write_text(cand.body, encoding="utf-8")
    return f"skills/{name} (updated skill)"


def apply_fact(repo: Path, cand: Candidate) -> str:
    if cand.edit == "new" and not cand.topic:
        raise MnemeError(f"candidate {cand.id}: fact candidate has no topic")
    line = cand.body.strip()
    bullet = units.parse_bullet_line(line, 1)

    if cand.edit == "new":
        path = repo / "facts" / f"{cand.topic}.md"
        if path.exists():
            meta, body = units.parse_frontmatter(path.read_text(encoding="utf-8-sig"))
        else:
            meta, body = {"topic": cand.topic}, ""
        for n, existing in enumerate(body.splitlines(), start=1):
            if existing.startswith("- ["):
                if units.parse_bullet_line(existing, n).topic_key == bullet.topic_key:
                    raise MnemeError(
                        f"candidate {cand.id}: topic key '{bullet.topic_key}' already exists"
                        f" in facts/{cand.topic}.md — expected an update edit"
                    )
        new_body = body.rstrip("\n")
        new_body = (new_body + "\n" if new_body else "") + line + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(units.serialize_frontmatter(meta, new_body), encoding="utf-8")
        return f"facts/{cand.topic}#{bullet.topic_key} (new fact)"

    if "#" not in cand.target_unit or not cand.target_unit.startswith("facts/"):
        raise MnemeError(f"candidate {cand.id}: malformed fact target_unit {cand.target_unit!r}")
    file_part, key = cand.target_unit.removeprefix("facts/").split("#", 1)
    path = repo / "facts" / f"{file_part}.md"
    if not path.exists():
        raise MnemeError(f"candidate {cand.id}: update target file {path.name} not found")
    meta, body = units.parse_frontmatter(path.read_text(encoding="utf-8-sig"))
    out_lines: list[str] = []
    replaced = False
    for n, existing in enumerate(body.splitlines(), start=1):
        if not replaced and existing.startswith("- ["):
            try:
                if units.parse_bullet_line(existing, n).topic_key == key:
                    out_lines.append(line)
                    replaced = True
                    continue
            except MnemeError:
                pass
        out_lines.append(existing)
    if not replaced:
        raise MnemeError(
            f"candidate {cand.id}: no bullet with topic key '{key}' in facts/{file_part}.md"
        )
    path.write_text(
        units.serialize_frontmatter(meta, "\n".join(out_lines) + "\n"), encoding="utf-8"
    )
    return f"facts/{file_part}#{key} (updated fact)"


@dataclass
class HarvestResult:
    target: str
    units: list[str] = field(default_factory=list)
    branch: str = ""
    commit: str = ""
    pr: str = ""
    mode: str = ""


def _regenerate_index(repo: Path) -> None:
    manifest = repo / ".claude-plugin" / "plugin.json"
    if not manifest.exists():
        return
    data = json.loads(manifest.read_text(encoding="utf-8"))
    (repo / "skills" / "knowledge-index").mkdir(parents=True, exist_ok=True)
    scaffold_mod.regenerate_index_skill(
        repo, str(data.get("name", repo.name)), str(data.get("description", ""))
    )


def apply_batch(
    home: Path, target_name: str, candidates: list[Candidate], *, push: bool = True
) -> HarvestResult:
    plugin = registry.get_plugin(home, target_name)
    if plugin is None:
        raise MnemeError(f"unknown harvest target: {target_name}")
    repo = Path(plugin.path)
    if not gitops.is_git_repo(repo):
        raise MnemeError(f"{repo} is not a git repository")
    if not gitops.is_clean(repo):
        raise MnemeError(f"{repo} has uncommitted changes — commit or stash them first")
    for cand in candidates:
        if cand.status == "quarantined":
            raise MnemeError(
                f"candidate {cand.id} is quarantined — redact and re-stage before harvesting"
            )

    gitops.sync_main(repo)
    result = HarvestResult(target=target_name, mode=plugin.mode)
    if plugin.mode == "pr":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        result.branch = f"mneme/harvest-{stamp}"
        gitops.create_branch(repo, result.branch)
    else:
        result.branch = "main"

    try:
        for cand in candidates:
            if cand.type == "skill":
                result.units.append(apply_skill(repo, cand))
            else:
                result.units.append(apply_fact(repo, cand))
        _regenerate_index(repo)
        issues = lint.lint_repo(repo)
        if lint.has_errors(issues):
            details = "; ".join(
                f"{i.code} {i.message}" for i in issues if i.severity == "error"
            )
            raise MnemeError(f"harvest fails repo lint: {details}")
        for cand in candidates:
            if scan.has_blockers(scan.scan_text(cand.body)):
                raise MnemeError(f"candidate {cand.id} re-scan found blocking findings")
    except MnemeError:
        gitops.restore(repo)
        if plugin.mode == "pr":
            gitops.git(repo, "checkout", "main")
            gitops.git(repo, "branch", "-D", result.branch)
        raise

    sources = [str(c.provenance.get("source", "unknown")) for c in candidates]
    result.commit = gitops.commit_harvest(repo, result.units, sources)

    if plugin.mode == "pr":
        if push and gitops.has_remote(repo):
            gitops.push_branch(repo, result.branch)
            title = f"knowledge: harvest ({len(result.units)} units)"
            result.pr = gitops.open_pr(repo, result.branch, title, "\n".join(result.units))
        else:
            result.pr = "no remote — branch is local only"
        gitops.git(repo, "checkout", "main")
    else:
        if push and gitops.has_remote(repo):
            gitops.push_main(repo)
            result.pr = "pushed to main"
        else:
            result.pr = "no remote — committed to local main"

    record = {
        "target": target_name,
        "branch": result.branch,
        "commit": result.commit,
        "mode": plugin.mode,
        "pr": result.pr,
        "units": result.units,
        "candidates": [c.id for c in candidates],
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    paths.ensure_layout(home)
    with paths.submitted_path(home).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    from . import staging as staging_mod

    for cand in candidates:
        staging_mod.remove_candidate(home, cand.id)
    return result
