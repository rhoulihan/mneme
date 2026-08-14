"""Applying approved candidates to knowledge-repo clones (spec §7.3)."""
from __future__ import annotations

import codecs
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import gitops, layout, lint, paths, registry, scan, units
from . import scaffold as scaffold_mod
from .errors import MnemeError
from .staging import Candidate


def _skill_name(cand: Candidate) -> str:
    meta, _body = units.parse_frontmatter(cand.body)
    name = str(meta.get("name", ""))
    if not name:
        raise MnemeError(f"candidate {cand.id}: skill body has no frontmatter name")
    return name


def _unit_path(root: Path, kind: str, name: str, what: str, *tail: str, suffix: str = "") -> Path:
    """A path under `root` (the repo's `<kind>` directory) built from a candidate name.

    ``root`` is passed in rather than derived, because facts live in one of two places
    (canonical under the router skill, or a legacy top-level `facts/`) — the containment
    proof below has to be made against the directory actually being written.

    Skill names and fact topics arrive as candidate frontmatter — model-generated or
    hand-placed text, i.e. untrusted input to a filesystem write. Unchecked, a name of
    `../../other-kb/skills/injected` writes into a *sibling* registered repo's working
    tree, and `../../../loose` writes outside every repo. Both escape the harvest's own
    safety net: `_abort` only restores the target repo, and lint (MN002) never sees a
    file that never landed in the target repo.

    Kebab-case is the unit-name contract everywhere else (lint MN002, scaffold,
    proposals, registry), and it is what makes a name exactly one literal path segment.
    The containment assert behind it is belt-and-braces: the write is what is dangerous,
    so it is proven in terms of the resolved path, not only the spelling of the name.
    """
    if not units.KEBAB_RE.fullmatch(name):
        raise MnemeError(f"{what} must be kebab-case: {name!r}")
    path = root.joinpath(name + suffix, *tail)
    if not path.resolve().is_relative_to(root.resolve()):
        raise MnemeError(f"{what} escapes {kind}/: {name!r}")
    return path


def _fact_path(repo: Path, stem: str, what: str) -> Path:
    """The file for topic `stem`: where it already lives, else where a new one goes.

    Existing layouts are searched first (canonical, then legacy) so appending to a topic
    a repo already carries at the top level cannot fork it into a second file under the
    router skill — two files, one unit id, half the bullets in each. The name is validated
    against whichever directory is actually being written, never trusted.

    A topic that does not exist yet goes to `facts_write_dir` — always canonical, even in
    a repo whose other facts still sit at the root. Following the legacy layout here is
    what kept pre-0.5 repos legacy forever; the root directory those files sit in is
    migrated wholesale instead (`layout.migrate_legacy_facts`).
    """
    for d in units.facts_dirs(repo):
        path = _unit_path(d, "facts", stem, what, suffix=".md")
        if path.exists():
            return path
    return _unit_path(units.facts_write_dir(repo), "facts", stem, what, suffix=".md")


def apply_skill(repo: Path, cand: Candidate) -> str:
    name = _skill_name(cand)
    skill_md = _unit_path(
        repo / "skills", "skills", name, f"candidate {cand.id}: skill name", "SKILL.md"
    )
    if cand.edit == "new":
        if skill_md.exists():
            raise MnemeError(
                f"candidate {cand.id}: skills/{name} already exists — expected an update edit"
            )
        # `skills/<name>` occupied by a *file* (or `skills` itself being one) makes mkdir
        # raise FileExistsError/NotADirectoryError. Those are repo-shape problems, not
        # bugs: they must read as MnemeError so the batch aborts through the guarded
        # path instead of escaping as a raw traceback.
        try:
            skill_md.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise MnemeError(
                f"candidate {cand.id}: cannot create skills/{name}/ in the knowledge repo:"
                f" {e.strerror or e}"
            ) from e
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


_BOM = "\ufeff"


def _read_raw(path: Path) -> tuple[str, str]:
    """Decoded text plus the byte-order mark to write back, if the file carried one.

    Fact applies are delta edits: everything mneme does not deliberately change has to
    survive byte-for-byte, and that includes a BOM an editor put there.
    """
    data = path.read_bytes()
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8"), _BOM
    return data.decode("utf-8"), ""


# Only CR/LF end a line. `str.splitlines` also breaks on \x0b, \x0c, \u2028 and friends,
# which inside a fact bullet are data: splitting there and rejoining would silently move
# bytes the delta edit promised not to touch.
_LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


def _lines_keepends(text: str) -> list[str]:
    return _LINE_RE.findall(text)


def _split_eol(line: str) -> tuple[str, str]:
    """Split one `splitlines(keepends=True)` line into (content, line ending)."""
    for eol in ("\r\n", "\n", "\r"):
        if line.endswith(eol):
            return line[: -len(eol)], eol
    return line, ""


def _body_start(lines: list[str]) -> int:
    """Index of the first body line, skipping a leading frontmatter block."""
    if not lines or lines[0].strip() != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i + 1
    raise MnemeError("unterminated frontmatter block")


def _dominant_eol(lines: list[str]) -> str:
    """The line ending the file already uses, so an appended bullet matches it."""
    for line in reversed(lines):
        _content, eol = _split_eol(line)
        if eol:
            return eol
    return "\n"


def apply_fact(repo: Path, cand: Candidate) -> str:
    if cand.edit == "new" and not cand.topic:
        raise MnemeError(f"candidate {cand.id}: fact candidate has no topic")
    line = cand.body.strip()
    bullet = units.parse_bullet_line(line, 1)

    if cand.edit == "new":
        path = _fact_path(repo, cand.topic, f"candidate {cand.id}: fact topic")
        text, bom = _read_raw(path) if path.exists() else ("", "")
        if not text.strip():
            # Only a genuinely new (or empty) topic file is written whole.
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                bom + units.serialize_frontmatter({"topic": cand.topic}, line + "\n"),
                encoding="utf-8", newline="",
            )
            return f"facts/{cand.topic}#{bullet.topic_key} (new fact)"

        lines = _lines_keepends(text)
        start = _body_start(lines)
        last_bullet = -1
        for i in range(start, len(lines)):
            content, _eol = _split_eol(lines[i])
            if not content.startswith("- ["):
                continue
            last_bullet = i
            try:
                existing_key = units.parse_bullet_line(content, i + 1).topic_key
            except MnemeError:
                # A malformed legacy bullet elsewhere in the file is not this
                # candidate's problem — every other reader (lint, verify, share diff,
                # the update path below) skips it, and refusing here would make the
                # whole topic permanently un-appendable in an adopted repo.
                continue
            if existing_key == bullet.topic_key:
                raise MnemeError(
                    f"candidate {cand.id}: topic key '{bullet.topic_key}' already exists"
                    f" in facts/{cand.topic}.md — expected an update edit"
                )

        if last_bullet >= 0:
            anchor, eol = _split_eol(lines[last_bullet])
            if not eol:  # file ended mid-line: terminate it before appending
                eol = _dominant_eol(lines)
                lines[last_bullet] = anchor + eol
            lines.insert(last_bullet + 1, line + eol)
        else:
            eol = _dominant_eol(lines)
            if lines:
                tail, tail_eol = _split_eol(lines[-1])
                if not tail_eol:
                    lines[-1] = tail + eol
            lines.append(line + eol)
        # newline="": the line endings in `lines` are the file's own, never retranslated.
        path.write_text(bom + "".join(lines), encoding="utf-8", newline="")
        return f"facts/{cand.topic}#{bullet.topic_key} (new fact)"

    if "#" not in cand.target_unit or not cand.target_unit.startswith("facts/"):
        raise MnemeError(f"candidate {cand.id}: malformed fact target_unit {cand.target_unit!r}")
    file_part, key = cand.target_unit.removeprefix("facts/").split("#", 1)
    path = _fact_path(repo, file_part, f"candidate {cand.id}: fact topic in target_unit")
    if not path.exists():
        raise MnemeError(f"candidate {cand.id}: update target file {path.name} not found")
    text, bom = _read_raw(path)
    lines = _lines_keepends(text)
    start = _body_start(lines)
    replaced = False
    for i in range(start, len(lines)):
        content, eol = _split_eol(lines[i])
        if not content.startswith("- ["):
            continue
        try:
            if units.parse_bullet_line(content, i + 1).topic_key != key:
                continue
        except MnemeError:
            continue
        # Exactly one line changes; its line ending, and every other byte in the file
        # (frontmatter comments, CRLF, trailing prose), is left alone.
        lines[i] = line + eol
        replaced = True
        break
    if not replaced:
        raise MnemeError(
            f"candidate {cand.id}: no bullet with topic key '{key}' in facts/{file_part}.md"
        )
    path.write_text(bom + "".join(lines), encoding="utf-8", newline="")
    return f"facts/{file_part}#{key} (updated fact)"


@dataclass
class HarvestResult:
    target: str
    units: list[str] = field(default_factory=list)
    branch: str = ""
    commit: str = ""
    pr: str = ""


def _regenerate_index(repo: Path, name: str = "", description: str = "") -> None:
    """Rewrite the router skill for whatever knowledge root this repo uses.

    ALWAYS, in either mode. This used to return early when there was no plugin manifest,
    which made a plain repo unusable rather than unsupported: the fact write created the
    knowledge root, nothing wrote the `SKILL.md` inside it, `lint_repo` then failed MN001
    on a directory mneme had just made itself, and the whole harvest rolled back. A repo
    with facts and no routing table is knowledge no agent is ever told exists — so the
    router is not an optional extra keyed on a manifest, it is part of writing a fact.
    """
    manifest = repo / ".claude-plugin" / "plugin.json"
    data: dict = {}
    if manifest.is_file():
        # A hand-edited or adopted repo can carry a manifest that is not valid JSON. That
        # is a repo problem the harvest must report (and roll back from), never a
        # ValueError escaping into a traceback.
        try:
            loaded = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise MnemeError(f"{manifest} is not valid JSON: {e}") from e
        if not isinstance(loaded, dict):
            raise MnemeError(f"{manifest} must contain a JSON object")
        data = loaded
    # Identity comes from the repo's own declaration first, the caller's registration
    # second, the checkout directory last. That order matters because the description IS
    # the routing prompt: a plain repo has no manifest, and falling straight to the
    # directory name published "durable facts from app" as the reason to consult a
    # payments knowledge base — a routing surface naming nothing an agent could match on.
    scaffold_mod.regenerate_index_skill(
        repo,
        str(data.get("name") or name or repo.name),
        str(data.get("description") or description or _scope_statement(repo)),
    )


def _scope_statement(repo: Path) -> str:
    """The repo's own `MNEME.md` scope statement, when it has one."""
    from . import routing

    return routing.read_scope_statement(repo / "MNEME.md")


def _abort(repo: Path, branch: str, base_sha: str) -> None:
    """Put the knowledge repo back exactly where the harvest found it: clean, on main.

    Best-effort by design — a failure while cleaning up must never mask the failure that
    triggered the abort, and leaving the repo dirty would wedge every later `share apply`
    on the `is_clean` precondition.
    """
    try:
        gitops.reset_hard(repo, base_sha)
        gitops.restore(repo)
        if gitops.current_branch(repo) != "main":
            gitops.git(repo, "checkout", "main")
        if branch and branch != "main":
            gitops.git(repo, "branch", "-D", branch)
    except MnemeError:
        pass


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

    # PR-only doctrine (spec §7.3): `sync_main` is the last thing that touches main, and
    # it only reads it. Every byte of knowledge mneme writes lands on the harvest branch.
    gitops.sync_main(repo)
    base_sha = gitops.head_sha(repo)
    result = HarvestResult(target=target_name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    result.branch = f"mneme/harvest-{stamp}"
    gitops.create_branch(repo, result.branch)

    # Apply + gate. `except Exception`, not `except MnemeError`: an unexpected failure
    # here (a malformed manifest, an odd repo shape, a filesystem error) must still hit
    # restore, or the repo is stranded dirty on the harvest branch and every later
    # `share apply` is refused by the is_clean precondition.
    try:
        # First thing on the branch, before a single candidate is applied. A pre-0.5 repo
        # is migrated by the next contribution it receives rather than being accommodated
        # forever (`units.facts_write_dir`), and the order is what makes the rest work: an
        # append to a topic that repo already had finds the file where it now lives, and
        # `_regenerate_index` below reads the moved files through `fact_files`, so the
        # routing table is correct for the new location by construction. PR-only holds —
        # the branch already exists and `main` was only ever read.
        migration = layout.migrate_legacy_facts(repo)
        for cand in candidates:
            if cand.type == "skill":
                result.units.append(apply_skill(repo, cand))
            else:
                result.units.append(apply_fact(repo, cand))
        _regenerate_index(repo, plugin.name)
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
        _abort(repo, result.branch, base_sha)
        raise
    except Exception as e:
        _abort(repo, result.branch, base_sha)
        raise MnemeError(f"harvest aborted — {type(e).__name__}: {e}") from e

    # Finalize: commit on the branch, offer it upstream, and return to main. Guarded the
    # same way — a rejected commit or a failed push after the gate passed must roll all
    # the way back, leaving staging intact so the identical harvest can simply be retried.
    sources = [str(c.provenance.get("source", "unknown")) for c in candidates]
    try:
        result.commit = gitops.commit_harvest(
            repo, result.units, sources, migration.body()
        )
        if push and gitops.has_remote(repo):
            gitops.push_branch(repo, result.branch)
            title = f"knowledge: harvest ({len(result.units)} units)"
            result.pr = gitops.open_pr(repo, result.branch, title, "\n".join(result.units))
        elif not gitops.has_remote(repo):
            result.pr = "no remote — branch left local; merge it or add a remote and push"
        else:
            result.pr = "push skipped (--no-push) — branch left local"
        # Back to main with the branch preserved: mneme hands the contribution over, it
        # never merges it.
        gitops.git(repo, "checkout", "main")
    except Exception as e:
        _abort(repo, result.branch, base_sha)
        raise MnemeError(
            f"harvest rolled back after the validation gate — {type(e).__name__}: {e};"
            " the repo is back on a clean main and the candidates are still staged"
        ) from e

    record = {
        "target": target_name,
        "branch": result.branch,
        "commit": result.commit,
        "pr": result.pr,
        "units": result.units,
        "candidates": [c.id for c in candidates],
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    from . import staging as staging_mod

    try:
        paths.ensure_layout(home)
        with paths.submitted_path(home).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    finally:
        # The knowledge is committed — past this point the candidates must leave staging
        # whatever happens to the ledger write, or every retry trips the "already exists"
        # guard and the candidate can never leave staging cleanly.
        for cand in candidates:
            try:
                staging_mod.remove_candidate(home, cand.id)
            except (MnemeError, OSError):
                pass
    return result
