"""Scaffold factory — generates governed knowledge-plugin repos (spec §5.1, §8)."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import gitops, lint, paths, registry, templates, units
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
        "MNEME.md": templates.render(templates.MNEME_MD, belongs=templates.BELONGS_PLUGIN, **subs),
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


# Everything `describe` reads is repo content: a contributor chose its length, its
# encoding and whether it parses. Each reader is bounded and each failure is an absent
# source rather than an exception, because this bundle exists to be shown to a user who is
# adopting a repo — not to audit it.
_README_CHARS = 1000
_TREE_ENTRIES = 40
_SUBJECTS = 15
_SUBJECT_CHARS = 120
_SIBLING_SCOPE_CHARS = 400
_SIBLING_FILE_CHARS = 20_000
_MANIFEST_CHARS = 200_000
_MANIFEST_FIELD_CHARS = 400

# Name/description live under different keys in each ecosystem's manifest; anything not
# listed here simply contributes no manifest source.
_MANIFESTS = (
    ("package.json", "json", ("name",), ("description",)),
    ("composer.json", "json", ("name",), ("description",)),
    ("pyproject.toml", "toml", ("project", "name"), ("project", "description")),
    ("Cargo.toml", "toml", ("package", "name"), ("package", "description")),
)


def _text(path: Path, limit: int) -> str:
    """At most `limit` characters of a file, or "" for anything that will not read.

    `read(limit)` rather than read-then-slice: a contributor picks the file's size, and
    slicing after the fact still pulls the whole thing into memory first. `errors="replace"`
    because a source mneme cannot decode is a source it does without, not a crash — this
    bundle reads a repo it did not write and does not get to fail it.
    """
    # A regular file, proven before opening. `open()` on a FIFO BLOCKS — a `README.md`
    # that is a named pipe hung the command forever, in a function whose docstring promises
    # every failure is an absent source. And a symlink is followed to wherever it points:
    # `README.md -> /etc/passwd` put the password file into a bundle handed to the model,
    # chosen by a repo somebody else wrote.
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _first_paragraph(repo: Path) -> str:
    """The README's first real paragraph — its one-line answer to "what is this".

    Headings and badge lines are skipped: a title repeats the repo name the bundle already
    carries, and a row of shields.io links is not a description of anything.
    """
    for name in ("README.md", "README.rst", "README.txt", "README"):
        raw = _text(repo / name, 20_000)
        if not raw:
            continue
        para: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                if para:
                    break
                continue
            if stripped.startswith("#") or stripped.startswith("[!["):
                continue
            para.append(stripped)
        if para:
            return " ".join(para)[:_README_CHARS]
    return ""


def _toml_field(text: str, table: str, key: str) -> str:
    """`key` from `[table]` in a TOML document, without a TOML parser.

    `tomllib` is Python 3.11+ and this project's floor is 3.10 with a stdlib-only runtime,
    so there is no backport to fall back on. `tests/e2e/test_release.py` reached exactly
    this conclusion, for exactly this reason, and said so in its docstring — and importing
    `tomllib` here anyway broke every test module on 3.10. CI caught it on the first push
    after the merge; nothing local did, because the development interpreter is 3.12.

    Scoped deliberately: the table header, then up to the next table, then the key. A
    document this cannot read contributes no manifest source, which is the same answer it
    gives for one that will not parse at all.
    """
    header = re.search(rf"^\[{re.escape(table)}\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if header is None:
        return ""
    m = re.search(rf"""^{re.escape(key)}\s*=\s*(["'])(.*?)\1\s*$""", header.group(1), re.M)
    return m.group(2) if m else ""


def _nested(data: object, keys: tuple[str, ...]) -> str:
    for key in keys:
        if not isinstance(data, dict):
            return ""
        data = data.get(key)
    return str(data) if isinstance(data, str) else ""


def _manifests(repo: Path) -> list[dict]:
    found: list[dict] = []
    for name, fmt, name_keys, desc_keys in _MANIFESTS:
        raw = _text(repo / name, _MANIFEST_CHARS)
        if not raw:
            continue
        if fmt == "json":
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                # A manifest that will not parse is not a source. It is also not an error:
                # adoption reads a repo it did not write and does not get to fail it.
                continue
            found_name, found_desc = _nested(data, name_keys), _nested(data, desc_keys)
        else:
            found_name = _toml_field(raw, name_keys[0], name_keys[1])
            found_desc = _toml_field(raw, desc_keys[0], desc_keys[1])
        # Capped like every other source. These two were bounded only by the 200 KB file
        # cap, so a manifest with a 150 000-character `name` put all of it into an agent's
        # context verbatim.
        entry = {
            "file": name,
            "name": found_name[:_MANIFEST_FIELD_CHARS],
            "description": found_desc[:_MANIFEST_FIELD_CHARS],
        }
        if entry["name"] or entry["description"]:
            found.append(entry)
    return found


def _shape(repo: Path) -> tuple[list[str], dict[str, int]]:
    """(top-level entries, extension histogram) from what git tracks.

    Tracked files, not a directory walk: `node_modules` and `.venv` are not this repo's
    shape, and a walk through them is unbounded work on somebody else's dependency tree.
    """
    try:
        listing = gitops.git_raw(repo, "ls-files", "-z")
    except MnemeError:
        return [], {}
    tops: list[str] = []
    langs: dict[str, int] = {}
    seen: set[str] = set()
    for rel in listing.split("\0"):
        if not rel:
            continue
        head = rel.split("/", 1)[0]
        if head not in seen and len(tops) < _TREE_ENTRIES:
            seen.add(head)
            tops.append(head)
        ext = rel.rsplit(".", 1)[-1] if "." in rel.rsplit("/", 1)[-1] else ""
        if ext:
            langs[ext] = langs.get(ext, 0) + 1
    return sorted(tops), dict(sorted(langs.items(), key=lambda kv: (-kv[1], kv[0]))[:12])


def _subjects(repo: Path) -> list[str]:
    """Recent commit subjects — what this repo has actually been busy with."""
    try:
        out = gitops.git_raw(repo, "log", "-n", str(_SUBJECTS), "--format=%s")
    except MnemeError:
        return []
    return [line.strip()[:_SUBJECT_CHARS] for line in out.splitlines() if line.strip()]


def _siblings(home: Path, exclude: str) -> list[dict]:
    """Every OTHER registered scope, so the draft can say where this one ends."""
    from . import routing

    out: list[dict] = []
    for plugin in registry.load_registry(home):
        if plugin.name == exclude:
            continue
        # `_text`, not `routing.read_scope_statement`: that helper reads the whole file with
        # `errors` unset, so one invalid UTF-8 byte in ANY registered repo's MNEME.md raised
        # out of `describe` and bricked adoption of every other repo, and a 400 MB sibling
        # was read whole to produce 400 characters.
        raw = _text(Path(plugin.path) / "MNEME.md", _SIBLING_FILE_CHARS)
        scope = routing.scope_statement_from(raw) if raw else ""
        out.append({"name": plugin.name, "scope": " ".join(scope.split())[:_SIBLING_SCOPE_CHARS]})
    return out


def describe(home: Path, name: str, *, as_plugin: bool | None = None) -> dict:
    """The raw material for a scope statement, for an agent to draft FROM.

    "What should this repo's scope be?" is a question almost nobody can answer cold, and
    the answer is the routing prompt every candidate fact is matched against — so a vague
    one quietly steals candidates from every sibling scope. Adoption proposes and the user
    corrects instead, and this is what the proposal is built from.

    Key ORDER is part of the contract, as it is for the classify and review bundles: the
    CLI serializes with `json.dumps`, which writes keys in insertion order, so the standing
    rule opens the document and closes it with every line of repo content in between.
    """
    plugin = registry.get_plugin(home, name)
    if plugin is None:
        raise MnemeError(f"plugin not registered: {name}")
    target = Path(plugin.path)
    if not target.is_dir():
        raise MnemeError(f"local clone missing: {target}")
    mode, why, _hint = _adopt_mode(target, as_plugin)
    tops, langs = _shape(target)
    agent_docs = [
        f for f in ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "MNEME.md")
        if (target / f).is_file()
    ]
    return {
        "instructions": templates.ADOPT_INSTRUCTIONS,
        "repo": {
            "name": name,
            "path": str(target),
            "mode": mode,
            "why": why,
            "knowledge_root": units.PLUGIN_ROOT if mode == "plugin" else units.PLAIN_ROOT,
            "sensitivity": plugin.sensitivity,
        },
        "sources": {
            "readme": _first_paragraph(target),
            "manifests": _manifests(target),
            "tree": tops,
            "languages": langs,
            "recent_subjects": _subjects(target),
            "agent_docs": agent_docs,
        },
        "siblings": _siblings(home, name),
        "standing_rule": templates.STANDING_RULE_REMINDER,
    }


@dataclass
class AdoptResult:
    """What adoption added, what it could only advise, and which mode it picked."""

    added: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    mode: str = "plugin"


def _adopt_mode(target: Path, as_plugin: bool | None) -> tuple[str, str, str | None]:
    """(mode, why, hint) — classified ONCE, and the repo is unambiguous in that mode after.

    ONLY a manifest auto-selects the invasive mode. Plugin adoption writes the plugin
    manifests, a root `CODEOWNERS`, repo-wide CI, and a `release.yml` that commits and
    pushes to `main` under `contents: write` — and an earlier version of this function
    escalated to all of that on finding a single `skills/<name>/SKILL.md`. That is what an
    ordinary repo using Claude Code has. It is not consent to become a marketplace plugin,
    and inferring consent from a file the repo already had is the annexation this whole
    change exists to prevent. Worse, once classified plugin, `units.skill_dirs` walks all of
    `skills/`, so one sibling directory without a SKILL.md yields MN001 and every later
    harvest aborts — the repo mneme could register and could not use, through the other door.

    The real case behind that heuristic — a hand-built knowledge repo that never got
    packaged — is still served, as a REPORTED ambiguity rather than a silent escalation. The
    user is one flag from the mode they want, and nothing was done to their repo to find out.

    `--plain` on a repo that carries a manifest is refused rather than obeyed: the manifest
    is what makes a repo a plugin, every later read and write resolves through it, and
    deleting it is repo content mneme does not edit. A mode has to be TRUE of the repo
    afterwards, not merely claimed by a flag.
    """
    manifest = units.is_plugin(target)
    established = units.established_root(target)
    established_mode = None
    if established is not None:
        established_mode = "plugin" if established.parent.name == "skills" else "plain"

    # A mode has to be TRUE of the repo afterwards, not merely claimed by a flag. Both
    # refusals below exist because obeying the flag would leave the repo in two modes at
    # once: two routers, two facts directories, and rows in each naming files that live in
    # the other. Adopt adds what is missing and never moves repo content, so it cannot
    # resolve that by relocating anything — it says so instead.
    if established_mode is not None and as_plugin is not None:
        asked = "plugin" if as_plugin else "plain"
        if asked != established_mode:
            rel = established.relative_to(target).as_posix()
            raise MnemeError(
                f"{target} already keeps its knowledge in {rel}/, so --{'as-plugin' if as_plugin else 'plain'}"
                f" would give it a second knowledge root while the first one stayed behind:"
                " two routers, and rows in each naming files that live under the other."
                " mneme does not move repo content, and there is no migration between the"
                f" two roots yet. Move {rel}/ yourself if you want the other mode, then"
                " adopt again."
            )
    if as_plugin is False and manifest:
        raise MnemeError(
            f"{target} carries .claude-plugin/plugin.json, which is what makes a repo a"
            " plugin — every later read and write resolves through it, so --plain would"
            " claim a mode that is not true of this repo. Remove the manifest yourself if"
            " that is what you want (mneme does not delete repo content), then adopt again."
        )
    if as_plugin is True:
        return "plugin", "requested with --as-plugin", None
    if as_plugin is False:
        return "plain", "requested with --plain", None
    if established_mode is not None:
        rel = established.relative_to(target).as_posix()
        return established_mode, f"the repo already keeps its knowledge in {rel}/", None
    if manifest:
        return "plugin", "the repo already carries .claude-plugin/plugin.json", None
    hint = None
    if _has_own_skills(target):
        hint = (
            "this repo carries skills/<name>/SKILL.md but no plugin manifest — adopted as"
            " plain, which leaves skills/ alone. If it is really a knowledge repo whose"
            " skills mneme should maintain, re-run with --as-plugin"
        )
    return "plain", "the repo is not a knowledge plugin, so mneme keeps to one directory", hint


def _has_own_skills(target: Path) -> bool:
    """Does `skills/` hold at least one `SKILL.md`? Never raises — it is only a hint.

    `iterdir()` on an unreadable directory raises `PermissionError`, and `is_dir()` follows
    links, so an unguarded walk let a repo mneme cannot even read crash the command, and a
    `skills -> ../elsewhere` link let content OUTSIDE the repo drive the classification.
    """
    skills = target / "skills"
    if skills.is_symlink() or not skills.is_dir():
        return False
    try:
        return any((d / "SKILL.md").is_file() for d in skills.iterdir() if d.is_dir())
    except OSError:
        return False


def adopt(
    home: Path, name: str, *, description: str = "", owner: str = "maintainers",
    as_plugin: bool | None = None,
) -> AdoptResult:
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
    mode, why, hint = _adopt_mode(target, as_plugin)
    result = AdoptResult(mode=mode)
    result.notes.append(f"mode: {mode} — {why}")
    if hint:
        result.notes.append(hint)

    if mode == "plugin":
        candidates, advisory = _plugin_files(target, subs, owner)
        root_rel = units.PLUGIN_ROOT
    else:
        candidates, advisory = _plain_files(target, subs, owner)
        root_rel = units.PLAIN_ROOT
    result.notes.extend(advisory)

    # Adoption seeds the canonical facts location, unconditionally — including in a repo
    # that still files facts at the top level. Skipping it there (Plan 10) left the adopted
    # repo with nowhere canonical to write, so its very next fact re-confirmed the legacy
    # layout: accommodation is what keeps a pre-0.5 repo legacy forever. The seeded
    # directory is empty, so it shadows nothing — every reader sweeps every layout
    # (`units.fact_files`) until `layout.migrate_legacy_facts` runs on the next
    # contribution. What adopt must not do is any of that migrating itself: it adds files
    # that are missing and never rewrites, moves or deletes repo content.
    candidates[f"{root_rel}/facts/.gitkeep"] = ""
    for rel, content in candidates.items():
        _write_missing(target, rel, content, result)
    result.notes.extend(_scope_warning(target, result))
    if f"{root_rel}/SKILL.md" in result.added:
        regenerate_index_skill(target, name, description, root_rel=root_rel)
    return result


def _write_missing(target: Path, rel: str, content: str, result: AdoptResult) -> None:
    """Create `target/rel` if it is genuinely absent — and never through a link.

    Two separate ways the old three-liner wrote outside the repo, both reachable from repo
    content a contributor can commit:

    `path.exists()` FOLLOWS symlinks, so a DANGLING link reads as a missing file, the
    "only add what is missing" test passes, and `write_text` creates the link's target —
    an arbitrary file at any path the user can write, chosen by somebody else's repository.
    Nothing about it shows up in `git status`, so the one safeguard adoption prescribes
    ("review and commit these files through your repo's normal process") cannot see it.
    `is_symlink()` first is what makes a link a thing that is THERE.

    `mkdir(parents=True, exist_ok=True)` accepts a symlinked parent just as happily, so
    `.github/workflows -> ../../elsewhere` redirects the write with no dangling leaf needed.
    `units.first_link_segment` proves the whole path, segment by segment.

    A failure is reported with what already landed. Adoption is not transactional and is not
    being made so — it writes independent files and rolling back could delete something the
    user had meanwhile edited — but silence about a half-finished adoption is the part that
    actually hurts, because the user cannot undo what they were never told about.
    """
    path = target / rel
    linked = units.first_link_segment(target, rel)
    if linked is not None:
        raise MnemeError(
            f"{linked} is a symlink, not a regular file or directory — refusing to write"
            f" {rel} through it: the file would land at the far end of the link, outside"
            " this repository and invisible to the `git status` you would review it with."
            f" Replace the link with a real file or directory (or remove it).{_landed(result)}"
        )
    # `is_symlink()` first is what makes a link a thing that is THERE. Redundant while
    # the proof above catches every link, and kept deliberately: a later narrowing of
    # that proof to parent segments only must not silently re-open the leaf case.
    if path.is_symlink() or path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        raise MnemeError(
            f"cannot write {rel}: {e.strerror or e}{_landed(result)}"
        ) from e
    result.added.append(rel)


def _scope_warning(target: Path, result: AdoptResult) -> list[str]:
    """Warn when the repo ends up with no routing prompt at all.

    "Never overwrites" cuts both ways: an existing `MNEME.md` is left alone, even a stub
    with a title and no `## Scope statement`. Adoption then reports success and the repo is
    registered with an EMPTY scope statement — and that statement is the prompt every
    candidate fact is matched against, so the repo is registered and unroutable. Silence
    about it is what made this hard to notice; `mneme context` would say
    `(no scope statement)` only if someone thought to look.
    """
    from . import routing

    if "MNEME.md" in result.added:
        return []
    if routing.read_scope_statement(target / "MNEME.md").strip():
        return []
    return [
        "MNEME.md already existed and was left alone, but it carries no `## Scope"
        " statement` section — that statement is the routing prompt every candidate fact is"
        " matched against, so nothing will route here until you add one by hand."
    ]


def _landed(result: AdoptResult) -> str:
    """What adoption already wrote, for a message that has to stop partway."""
    if not result.added:
        return " Nothing was written."
    return " Already written, and yours to keep or remove: " + ", ".join(result.added)


def _plugin_files(target: Path, subs: dict, owner: str) -> tuple[dict[str, str], list[str]]:
    """What a repo whose PURPOSE is knowledge gets: the full plugin scaffold."""
    files = {
        "MNEME.md": templates.render(
            templates.MNEME_MD, belongs=templates.BELONGS_PLUGIN, **subs
        ),
        "CONTRIBUTING.md": templates.render(templates.CONTRIBUTING_MD, **subs),
        ".github/workflows/validate.yml": templates.VALIDATE_YML,
        ".github/workflows/release.yml": templates.RELEASE_YML,
        ".claude-plugin/plugin.json": templates.render_json(templates.PLUGIN_JSON, **subs),
        ".claude-plugin/marketplace.json": templates.render_json(
            templates.MARKETPLACE_JSON, **subs
        ),
        f"{units.PLUGIN_ROOT}/SKILL.md": templates.render(
            templates.INDEX_SKILL_MD, index_name="knowledge-index", **subs
        ),
    }
    # The same care plain mode takes. This path took none, so adopting a repo that already
    # had `.github/CODEOWNERS` wrote a second, repo-wide `* @maintainers` beside it —
    # re-routing every pull request in the repo to people who never agreed to that.
    rule = templates.render(templates.CODEOWNERS, **subs)
    existing = _existing_codeowners(target)
    notes: list[str] = []
    if existing is None:
        files["CODEOWNERS"] = rule
    else:
        notes.append(
            f"{existing} already exists and was left alone — add `* @{owner}` (or a"
            " narrower rule) if knowledge review should route to that team"
        )
    return files, notes


def _existing_codeowners(target: Path) -> str | None:
    """Where this repo already keeps CODEOWNERS, if it does.

    GitHub reads it from the root, `.github/`, or `docs/` and nowhere else, so a scoped rule
    cannot be tucked inside the knowledge root — and when one exists the rule is REPORTED
    rather than appended. Adopt adds missing files and never edits repo content, and a file
    that routes code review is the last place to make an exception.
    """
    for rel in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        if (target / rel).is_file():
            return rel
    return None


def _plain_files(target: Path, subs: dict, owner: str) -> tuple[dict[str, str], list[str]]:
    """What an application gets: one directory, and nothing of its own taken over.

    No manifests (it is not being published as a plugin), no `release.yml` (it bumps a
    version in a manifest that will not exist), no root `CONTRIBUTING.md` (the repo has
    its own, about its own code), and CI that fires only when the knowledge changes.
    """
    root = units.PLAIN_ROOT
    files = {
        "MNEME.md": templates.render(
            templates.MNEME_MD, belongs=templates.BELONGS_PLAIN, **subs
        ),
        f"{root}/SKILL.md": templates.render(
            templates.INDEX_SKILL_MD, index_name=root, **subs
        ),
        f"{root}/CONTRIBUTING.md": templates.render(
            templates.CONTRIBUTING_PLAIN_MD, knowledge_root=root, **subs
        ),
        ".github/workflows/mneme-validate.yml": templates.validate_yml(root),
    }
    rule = templates.render(templates.CODEOWNERS_SCOPED, knowledge_root=root, owner=owner)
    notes: list[str] = []
    existing = _existing_codeowners(target)
    if existing is None:
        files["CODEOWNERS"] = rule
    else:
        line = rule.strip().splitlines()[-1]
        notes.append(
            f"{existing} already exists and was left alone —"
            f" add this line to route knowledge review: {line}"
        )
    return files, notes


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


def regenerate_index_skill(
    target: Path, name: str, description: str, *, root_rel: str | None = None
) -> Path:
    """Rewrite the router skill. `root_rel` overrides where it goes.

    Where the router goes is normally the repo's mode: a plugin's belongs in
    `skills/knowledge-index/` where Claude Code discovers it, a plain repo's in
    `mneme-index/` where it collides with nothing the application owns. The skill is named
    for the directory it lands in, because MN003 requires exactly that.

    `root_rel` exists because `adopt` decides the mode ITSELF — `--plain` and `--as-plugin`
    are the user overriding the classification — and re-deriving it here from
    `units.knowledge_root` discarded that decision. `adopt --plain` on a repo that carries a
    manifest reported `mneme-index/SKILL.md`, then wrote `skills/knowledge-index/SKILL.md`:
    the one directory `--plain` promises never to claim, absent from the reported list, with
    the reported router left behind as an un-regenerated stub.

    That exact case is now refused outright (`_adopt_mode` will not give a repo a second
    knowledge root), so the two answers no longer diverge and no test can tell them apart.
    The parameter stays because the caller stating its decision is what makes a silent
    disagreement impossible if either rule moves again — this function must not re-derive
    something its caller has already settled.
    """
    root = target / root_rel if root_rel else units.knowledge_root(target)
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
        # The path a reader of THIS router can actually follow. Rows used to be
        # `facts/<name>` unconditionally — correct only for facts sitting inside the
        # router's own directory. With more than one root that made every other row a dead
        # link: a repo adopted plain and later re-adopted `--as-plugin` got a new router
        # listing `facts/chargebacks.md` next to itself while the fact stayed in
        # `mneme-index/facts/`, and lint reported nothing. By this function's own rule — a
        # topic missing from this table is a topic no agent is told exists — every topic in
        # that repo had become unreachable while the table looked complete.
        rel = f.relative_to(target).as_posix()
        inside = f.parent == root / "facts"
        entries.append((topic, f"facts/{f.name}" if inside else rel, count))

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
