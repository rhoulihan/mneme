"""Classify rails — branch discipline around the prompt-driven librarian pass (spec §7.7).

Classification itself is LLM judgment over repo structures that vary, so it lives in the
session. These rails are the deterministic frame around it: the directory the user is
standing in must resolve to a registered knowledge plugin, the work happens on a
`mneme/classify-*` branch, and `main` is never written (Plan 09 doctrine).

Review extraction (spec §7.8) needs the identical frame — an agent editing the same
working tree under the same gates — differing only in the branch namespace, the commit
subject, and the ledger kind. So the rails take a `kind` and both flows run the SAME code:
a gate the review rail skipped would be a gate that stopped holding for knowledge arriving
from strangers, which is the traffic that needs it most.

Plan 12 adds a third kind, "migrate", for the repo that is simply old: a pre-0.5 layout
nobody has harvested into since, which therefore never reaches the branch flows that would
have migrated it. It is the same rail with no session in the middle — `migrate()` runs
begin and finalize in one call, because there is nothing for an agent to decide between
them.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import gitops, harvest, layout, lint, paths, scan, templates, units
from .errors import MnemeError

# The kind word is the whole difference between the rails: it names the branch namespace,
# the commit subject, the ledger record, and every message the user reads.
#
# `_RAIL_KINDS` is narrower than the set of kinds: it is the kinds a repo can be STANDING ON
# between two commands — what `_active_rail` reports and `_begin` refuses. "migrate" is
# deliberately not among them: it has no begin and no abort of its own, so a
# `mneme/migrate-*` branch only exists inside the one call that creates it, and telling a
# user to "finalize or abort" a branch no command can finalize or abort would be advice
# they cannot take.
_RAIL_KINDS = ("classify", "review")

# The generated router skill, `skills/knowledge-index/` — the directory the canonical facts
# live inside. Never an integration destination (see `_is_integration_path`).
_INDEX_SKILL_DIR = units.FACTS_CANONICAL.rsplit("/", 1)[0] + "/"


def _branch_prefix(kind: str) -> str:
    return f"mneme/{kind}-"


BRANCH_PREFIX = _branch_prefix("classify")
REVIEW_BRANCH_PREFIX = _branch_prefix("review")
MIGRATE_BRANCH_PREFIX = _branch_prefix("migrate")


def resolve(home: Path, cwd: Path):
    """The registered plugin containing `cwd`, plus its repo root.

    The directory IS the argument — classify never takes a plugin name — so this is the
    one place that turns "where the user is" into "which repo may be rewritten", and the
    failure message has to tell them exactly how to get a directory that qualifies.
    """
    from . import routing

    scope = routing.plugin_for_path(home, cwd)
    if scope is None:
        raise MnemeError(
            "this directory is not inside a registered knowledge plugin —"
            " cd into one or register it first (/mneme:register)"
        )
    repo = Path(scope.path)
    if not gitops.is_git_repo(repo):
        raise MnemeError(f"{repo} is not a git repository")
    return scope, repo


def _active_rail(repo: Path) -> str | None:
    """The kind of rail branch this repo is standing on, or None."""
    branch = gitops.current_branch(repo)
    for kind in _RAIL_KINDS:
        if branch.startswith(_branch_prefix(kind)):
            return kind
    return None


def _begin(home: Path, cwd: Path, kind: str) -> str:
    _scope, repo = resolve(home, cwd)
    # Order matters: an already-active rail branch is the more specific diagnosis, and
    # such a branch is usually dirty by design (the agent is mid-edit) — reporting it as
    # "uncommitted changes" would send the user to stash work the abort rail exists for.
    # Either kind blocks either begin: both flows rewrite the one working tree, so a
    # review extraction started mid-classify would deliver the librarian's edits too.
    active = _active_rail(repo)
    if active is not None:
        raise MnemeError(f"a {active} branch is already active — finalize or abort it first")
    if not gitops.is_clean(repo):
        raise MnemeError(f"{repo} has uncommitted changes — commit or stash them first")
    gitops.sync_main(repo)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"{_branch_prefix(kind)}{stamp}"
    gitops.create_branch(repo, branch)
    return branch


def _abort(home: Path, cwd: Path, kind: str) -> None:
    _scope, repo = resolve(home, cwd)
    branch = gitops.current_branch(repo)
    # Deliberately the one prefix, not both: `mneme review abort` deleting a classify
    # branch would discard a pass its user never asked to end.
    if not branch.startswith(_branch_prefix(kind)):
        raise MnemeError(f"not on a {kind} branch — nothing to abort")
    gitops.restore(repo)
    gitops.git(repo, "checkout", "main")
    gitops.git(repo, "branch", "-D", branch)


def begin(home: Path, cwd: Path) -> str:
    return _begin(home, cwd, "classify")


def abort(home: Path, cwd: Path) -> None:
    _abort(home, cwd, "classify")


def review_begin(home: Path, cwd: Path) -> str:
    return _begin(home, cwd, "review")


def review_abort(home: Path, cwd: Path) -> None:
    _abort(home, cwd, "review")


def _fact_entries(repo: Path, notes: list[str]) -> list[dict]:
    # `units.fact_files` sweeps both layouts (canonical first): the librarian has to *see*
    # every fact, and a repo mid-migration can carry both.
    entries: list[dict] = []
    for f in units.fact_files(repo):
        rel = f.relative_to(repo).as_posix()
        try:
            text = f.read_text(encoding="utf-8-sig")
            meta, body = units.parse_frontmatter(text)
        except (MnemeError, OSError, UnicodeDecodeError) as e:
            notes.append(f"{rel}: unreadable ({e})")
            continue
        topic = str(meta.get("topic", f.stem))
        # Absolute line numbers, so the librarian can point at the bullet it moved.
        offset = len(text.splitlines()) - len(body.splitlines())
        for n, line in enumerate(body.splitlines(), start=1):
            if not line.startswith("- ["):
                continue
            try:
                bullet = units.parse_bullet_line(line, n)
            except MnemeError:
                notes.append(f"{rel}:{offset + n}: malformed fact bullet — left in place")
                continue
            entries.append(
                {
                    "file": rel,
                    "topic": topic,
                    "line": offset + n,
                    "category": bullet.category,
                    "text": bullet.text,
                    "tags": bullet.tags,
                    "verified": bullet.verified or "",
                    # Physical location never enters the id: a fact keeps its identity
                    # (and its declined-ledger / similar-to continuity) across the move.
                    "unit_id": units.fact_unit_id(f.stem, bullet.text),
                }
            )
    return entries


def _skill_entries(repo: Path, notes: list[str]) -> list[dict]:
    entries: list[dict] = []
    skills_dir = repo / "skills"
    if not skills_dir.is_dir():
        return entries
    for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        # knowledge-index is generated from the fact files — never a destination.
        if d.name == "knowledge-index":
            continue
        skill_md = d / "SKILL.md"
        rel_dir = d.relative_to(repo).as_posix()
        if not skill_md.is_file():
            notes.append(f"{rel_dir}: no SKILL.md — not a destination")
            continue
        try:
            meta, _body = units.parse_frontmatter(skill_md.read_text(encoding="utf-8-sig"))
        except (MnemeError, OSError, UnicodeDecodeError) as e:
            notes.append(f"{rel_dir}/SKILL.md: unreadable ({e})")
            continue
        entries.append(
            {
                "name": str(meta.get("name", d.name)),
                "description": str(meta.get("description", "")),
                "dir": rel_dir,
                "files": sorted(
                    p.relative_to(repo).as_posix() for p in d.rglob("*") if p.is_file()
                ),
            }
        )
    return entries


def bundle(home: Path, cwd: Path) -> dict:
    """Everything the in-session librarian needs, and nothing it has to guess.

    Key ORDER is part of the contract: the CLI serializes this dict with `json.dumps`,
    which writes keys in insertion order, so an agent reads it top to bottom. The
    instructions — which open with the standing rule — therefore come first, before the
    fact and skill text this repo's contributors wrote, and the rule is restated last.
    Shipping the rule as the final key, as this bundle used to, meant every injection in
    the quoted content was read before the sentence that disarms it.
    """
    scope, repo = resolve(home, cwd)
    notes: list[str] = []
    return {
        "instructions": templates.CLASSIFY_INSTRUCTIONS,
        "plugin": scope.name,
        "repo": str(repo),
        "facts": _fact_entries(repo, notes),
        "skills": _skill_entries(repo, notes),
        "legacy_layout": (repo / "facts").is_dir(),
        "notes": notes,
        "standing_rule": templates.STANDING_RULE_REMINDER,
    }


def _legacy_conflicts(repo: Path) -> list[str]:
    """Filenames both fact layouts carry — the one thing this rail hands back to the agent.

    Checked BEFORE the finalize rail touches anything, because the rail's failure path is a
    hard reset: raising from inside the migration destroyed the pass's own work (for review,
    an extraction the user had already approved, with nothing staged to retry from) for a
    condition the agent can fix in one edit.

    `layout.migrate_legacy_facts` can now MERGE a colliding pair rather than refuse it, so
    this is no longer the only possible answer — but it is still the right one HERE. The
    collision on this rail is one the agent has just manufactured, in the working tree,
    with the bundle's `facts_dir` naming the file it should have written to; asking it to
    put the bullet in the right file is better than folding two versions together and
    sending a human the difference. The harvest has no such author to ask, so it takes the
    merge.
    """
    legacy = repo / "facts"
    if not legacy.is_dir():
        return []
    canonical = repo / units.FACTS_CANONICAL
    conflicts: list[str] = []
    for src in sorted(p for p in legacy.rglob("*") if p.is_file()):
        rel = src.relative_to(legacy).as_posix()
        if src.name == ".gitkeep":
            continue  # both layouts carrying a placeholder is not a conflict
        if (canonical / rel).exists():
            conflicts.append(rel)
    return conflicts


def _legacy_conflict_error(kind: str, conflicts: list[str]) -> MnemeError:
    merges = "; ".join(
        f"merge facts/{rel} into {units.FACTS_CANONICAL}/{rel} by hand" for rel in conflicts
    )
    return MnemeError(
        f"both fact layouts carry {', '.join(conflicts)} — {merges}, then run"
        f" 'mneme {kind} finalize' again"
    )


def _nothing_to_do(kind: str) -> str:
    """What a rail says when it reaches the gates with nothing to deliver.

    Migrate does not get the generic sentence, because the generic sentence describes an
    absence of EDITS — true of every migrate pass, which is not an editing session at all.
    The user asked for one specific thing and the answer is about the repo: there is no
    legacy directory here. Naming the missing directory is also the whole diagnosis, since
    a repo that has already been migrated looks exactly like a repo that never needed it.
    """
    if kind == "migrate":
        return "no legacy facts directory — nothing to migrate"
    return (
        f"nothing to {kind} — no edits were made and no legacy facts needed"
        f" migrating; the {kind} branch has been discarded"
    )


def _named_in(rel: str, notes: list[str]) -> bool:
    """Does one of the migration's own notes already name exactly this path?

    A moved or merged file reaches `_changed_files` as well, and reporting it twice in one
    commit body invites a reviewer to look for a second change that does not exist. A bare
    substring test would go wrong the other way and suppress a top-level `README.md` merely
    because some note mentioned `facts/README.md` — the path would vanish from the commit
    body, the PR body and the ledger while staying in the diff.

    So the match is the WHOLE path with its boundaries checked, not a token. Splitting the
    note on whitespace (the previous form) cannot see a path that contains a space, and
    `facts/my deploys.md` is repo content this module's threat model already assumes: it
    was reported twice, once inside its note and again as a bare changed path. The notes
    are prose in three shapes — `a -> b`, `a merged into b (n bullets)`, `a: …` — so a path
    ends at a space, a colon, or the end of the note, and begins at a space or the start.
    """
    for note in notes:
        start = 0
        while (i := note.find(rel, start)) >= 0:
            j = i + len(rel)
            if (i == 0 or note[i - 1] == " ") and (j == len(note) or note[j] in " :"):
                return True
            start = i + 1
    return False


def _changed_files(repo: Path) -> list[str]:
    """Every path this classify pass touches — committed on the branch or still working.

    Both queries run in `-z` form and read through `git_raw`: NUL-separated paths are
    never quoted or line-split, so a filename containing a space, a quote, or a newline
    reaches the secret-scan gate intact instead of being silently skipped.

    `--untracked-files=all` is load-bearing, not tidiness: by default git collapses a
    wholly-untracked directory into a single `dir/` record, and a *directory* is not a
    file the scan gate can read — yet `git add -A` commits every file beneath it. A brand
    new skill is the mainline classify outcome, so that default would let the one case the
    librarian is most likely to produce walk past the secret scan.
    """
    changed: set[str] = set()
    for path in gitops.git_raw(repo, "diff", "-z", "--name-only", "main...HEAD").split("\0"):
        if path:
            changed.add(path)
    # A rename record spans two fields — `XY <path>\0<original>\0` — and the original is
    # gone from the working tree, so it is consumed and dropped.
    entries = [
        e
        for e in gitops.git_raw(
            repo, "status", "--porcelain", "-z", "--untracked-files=all"
        ).split("\0")
        if e
    ]
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:
            i += 1
        changed.add(path)
    return sorted(changed)


# UTF-8 is what mneme writes, but the gate has to hold over whatever the librarian's
# editor produced. UTF-32 is tried before UTF-16 because a UTF-32 file also decodes
# (into interleaved NULs) under UTF-16, while the reverse practically never happens.
_SCAN_CODECS = ("utf-8-sig", "utf-32", "utf-16")


def _scannable_text(path: Path) -> str | None:
    """Best-effort text for the secret scan — an odd encoding is never a free pass.

    The last resort is a lossy UTF-8 decode: undecodable bytes become replacement
    characters and any ASCII credential sitting among them still reaches the rules.
    Only a file that cannot be read at all yields None.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    for codec in _SCAN_CODECS:
        try:
            return raw.decode(codec)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def _scan_gate(repo: Path, changed: list[str], kind: str) -> None:
    for rel in changed:
        path = repo / rel
        if not path.is_file():
            continue  # deleted or renamed away — nothing left to leak
        text = _scannable_text(path)
        if text is None:
            continue  # unreadable: lint owns shape, the scan owns text
        findings = scan.scan_text(text)
        if scan.has_blockers(findings):
            rules = ", ".join(sorted({f.rule for f in findings if f.severity == scan.BLOCK}))
            raise MnemeError(f"{kind} fails the secret scan: {rel} ({rules})")


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _main_fact_bullets(repo: Path) -> list[tuple[str, str]]:
    """`(file, text)` for every fact bullet committed on `main`.

    Read from the ref rather than the working tree, because the working tree IS the thing
    under suspicion — the pass may already have deleted the file whose loss we are
    checking for. Path selection mirrors `units.fact_files`: the `*.md` directly inside
    either facts layout, canonical first. A bullet `main` already carries malformed is
    skipped; the branch cannot be blamed for damage it did not do.
    """
    prefixes = (f"{units.FACTS_CANONICAL}/", "facts/")
    bullets: list[tuple[str, str]] = []
    listing = gitops.git_raw(repo, "ls-tree", "-r", "-z", "--name-only", "main")
    for rel in listing.split("\0"):
        if not rel.endswith(".md"):
            continue
        if not any(rel.startswith(p) and "/" not in rel[len(p) :] for p in prefixes):
            continue
        try:
            _meta, body = units.parse_frontmatter(gitops.git_raw(repo, "show", f"main:{rel}"))
        except (MnemeError, UnicodeDecodeError):
            # A fact file mneme cannot read was already invisible to lint, the index, and
            # search; making it a wall every finalize hits would not preserve it.
            continue
        for n, line in enumerate(body.splitlines(), start=1):
            if not line.startswith("- ["):
                continue
            try:
                bullets.append((rel, units.parse_bullet_line(line, n).text))
            except MnemeError:
                continue
    return bullets


def _branch_fact_texts(repo: Path) -> set[str]:
    """Normalized text of every fact bullet the branch's working tree still carries."""
    texts: set[str] = set()
    for f in units.fact_files(repo):
        try:
            _meta, body = units.parse_frontmatter(f.read_text(encoding="utf-8-sig"))
        except (MnemeError, OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(body.splitlines(), start=1):
            if not line.startswith("- ["):
                continue
            try:
                texts.add(_normalized(units.parse_bullet_line(line, n).text))
            except MnemeError:
                continue
    return texts


def _is_integration_path(rel: str) -> bool:
    """Is this changed path skill PROSE a fact could have been integrated into?

    "Under `skills/`" is not the test, because the canonical facts directory lives under
    `skills/` too — `skills/knowledge-index/facts/`. Counting a fact FILE as an integration
    destination let the gate be satisfied by the very file whose bullet went missing: a
    bullet rewritten as prose in place, or moved into `facts/archive/` where
    `units.fact_files` (a flat `*.md` glob) never looks again, both left the sentence
    "accounted for" while every reader — lint, the index build, search, the classify
    bundle — had lost it. The rest of the router skill is generated from the fact files, so
    it is no destination either.
    """
    return rel.startswith("skills/") and not rel.startswith(_INDEX_SKILL_DIR)


def _integration_text(repo: Path, changed: list[str]) -> str:
    """One normalized blob of every skill file this pass touched — where facts go to live.

    Only files the pass CHANGED count: an integration is something this branch wrote.

    This was briefly widened to the whole skill tree, to let a librarian drop a fact whose
    sentence already sat in a skill nobody edited. The justification given was that the
    match is "a whole normalized fact SENTENCE, not a phrase, so a coincidence is remote".
    That was simply false: the test below is `text not in blob` — Python substring
    containment against every skill flattened to one line — so the widened form let a fact
    be retired by a four-word prefix of unrelated prose, by a heading joined to the body
    under it, by a skill's frontmatter `description:`, by a repo README that happens to sit
    under `skills/`, by a file `.gitignore` keeps out of the commit entirely, and by an
    anti-pattern section quoting the fact in order to REFUTE it. Ten scenarios flipped from
    refused to accepted, each one knowledge leaving the repo with nothing said about it.

    The case that motivated widening is real, and `--retire` is the honest answer to it:
    the librarian names the unit that covers the fact, the claim is checked for shape, and
    a human reads it in the pull request. A gate should not guess at coverage it cannot
    verify — it should make somebody say so.
    """
    parts: list[str] = []
    for rel in changed:
        if not _is_integration_path(rel):
            continue
        path = repo / rel
        if not path.is_file():
            continue  # deleted on the branch — nothing preserved there
        text = _scannable_text(path)
        if text is not None:
            parts.append(_normalized(text))
    return "\n".join(parts)


_RETIRE_SEP = "="


def _parse_retirements(retire: list[str] | None) -> list[tuple[str, str]]:
    """`<retired-unit-id>=<covering-unit-id>` pairs, as given on the command line.

    `=` is the separator because a unit id is `skills/<name>` or `facts/<stem>#<key>` and
    neither shape can contain one, so the split is unambiguous without quoting.
    """
    pairs: list[tuple[str, str]] = []
    for raw in retire or []:
        retired, sep, covering = raw.partition(_RETIRE_SEP)
        if not sep or not retired.strip() or not covering.strip():
            raise MnemeError(
                f"--retire expects <retired-unit-id>{_RETIRE_SEP}<covering-unit-id>,"
                f" got {layout._safe(raw)!r}"
            )
        pairs.append((retired.strip(), covering.strip()))
    return pairs


def _branch_unit_ids(repo: Path) -> set[str]:
    """Every unit id the branch still carries — the ids a retirement may point AT."""
    ids = {
        units.skill_unit_id(d.name)
        for d in sorted((repo / "skills").glob("*"))
        if d.is_dir() and (d / "SKILL.md").is_file()
    }
    for f in units.fact_files(repo):
        try:
            _meta, body = units.parse_frontmatter(f.read_text(encoding="utf-8-sig"))
        except (MnemeError, OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(body.splitlines(), start=1):
            if not line.startswith("- ["):
                continue
            try:
                ids.add(units.fact_unit_id(f.stem, units.parse_bullet_line(line, n).text))
            except MnemeError:
                continue
    return ids


def _accept_retirements(
    repo: Path, pairs: list[tuple[str, str]], main_facts: dict[str, list[str]]
) -> tuple[set[str], list[str]]:
    """Validate every declaration; return the retired fact TEXTS and the lines to report.

    mneme cannot tell whether the covering unit really says the same thing — that is the
    human's call at the pull request, which is why every accepted declaration is printed
    there. What it CAN do is refuse a declaration whose parts are not real, so the claim a
    reviewer reads is at least about two units that exist.

    Retired TEXTS, not ids, because a unit id does not identify a fact. `normalize_topic_key`
    is the first six words of the sentence, so two bullets in one topic file — routine,
    since every bullet in it is about the same subject — share an id. Excusing by id let a
    single declaration retire every colliding bullet at once while naming only one of them
    in the pull request: the rest left with nothing said about them, which is the whole
    thing this gate exists to prevent. An ambiguous id is refused rather than guessed at.
    """
    if not pairs:
        return set(), []
    on_branch_ids = _branch_unit_ids(repo)
    on_branch_texts = _branch_fact_texts(repo)
    declared = {retired_id for retired_id, _ in pairs}
    retired_texts: set[str] = set()
    lines: list[str] = []
    for retired_id, covering_id in pairs:
        if retired_id == covering_id:
            raise MnemeError(f"--retire {layout._safe(retired_id)}: a unit cannot cover itself")
        texts = main_facts.get(retired_id, [])
        if not texts:
            raise MnemeError(
                f"--retire {layout._safe(retired_id)}: not a fact on main — nothing to retire"
                " (check the unit id against `mneme classify prepare`)"
            )
        if len(texts) > 1:
            raise MnemeError(
                f"--retire {layout._safe(retired_id)}: that unit id names"
                f" {len(texts)} different bullets on main, because a unit id is only the"
                " first six words of a sentence — retiring by it would remove all of them"
                " while naming one. Reword or split the bullets so their ids differ, or"
                " keep them"
            )
        if _normalized(texts[0]) in on_branch_texts:
            raise MnemeError(
                f"--retire {layout._safe(retired_id)}: that fact is still present on the"
                " branch — declare a retirement only for a fact this pass removed"
            )
        if covering_id in declared:
            raise MnemeError(
                f"--retire {layout._safe(retired_id)}: covering unit"
                f" {layout._safe(covering_id)} is itself being retired by this pass —"
                " a retirement must point at knowledge that SURVIVES it"
            )
        if covering_id not in on_branch_ids:
            raise MnemeError(
                f"--retire {layout._safe(retired_id)}: covering unit"
                f" {layout._safe(covering_id)} does not exist on the branch — a retirement"
                " must point at knowledge that survives this pass"
            )
        retired_texts.add(_normalized(texts[0]))
        lines.append(f"{layout._safe(retired_id)} — covered by {layout._safe(covering_id)}")
    return retired_texts, lines


def _preservation_gate(
    repo: Path, changed: list[str], kind: str, retired: set[str] | None = None
) -> None:
    """Knowledge on `main` may be moved or integrated by this pass — never dropped silently.

    A fact is accounted for when its sentence is still a bullet in some fact file, appears
    verbatim inside a skill on the branch, or has been explicitly RETIRED with a
    declaration naming the unit that covers it. The first two are proof; the third is a
    stated claim, checked for shape by `_accept_retirements` and for substance by the human
    reading the pull request it is printed in.

    That is deliberately a floor and not a judgement of the integration's quality: mneme
    cannot tell a faithful summary from a lossy one, but it can tell that the original
    sentence still exists somewhere — which is also the better provenance — and it can
    refuse to let one vanish without anybody saying so.
    """
    retired = retired or set()  # normalized TEXTS, not ids — see `_accept_retirements`
    on_branch = _branch_fact_texts(repo)
    integrated = _integration_text(repo, changed)
    lost = [
        f"{rel}: {text[:80]}"
        for rel, text in _main_fact_bullets(repo)
        if _normalized(text) not in on_branch
        and _normalized(text) not in integrated
        and _normalized(text) not in retired
    ]
    if lost:
        raise MnemeError(
            f"{kind} would lose knowledge that is committed on main — "
            + "; ".join(lost)
            + "; facts may move, but never vanish — integrate the content, leave the fact"
            " in place, or retire it with"
            " `--retire <unit-id>=<covering-unit-id>` naming the unit that already says it"
        )


def _commit(
    repo: Path, plugin: str, kind: str, unit_lines: list[str], base_sha: str
) -> str:
    """Commit whatever the pass produced; deliver what is already committed unchanged.

    A librarian who commits their own edits on the classify branch — and an index
    regeneration that is then a no-op — leaves nothing in the working tree. That is a
    finished classify pass, not an empty one: the emptiness gate in `finalize` already
    accepted the branch as classifiable because it is ahead of `main`. Demanding a fresh
    commit here would raise into the rollback path and hard-reset the branch away, so the
    one thing the gate acknowledged is the one thing that must never be destroyed.
    """
    gitops.git(repo, "add", "-A")
    if gitops.git(repo, "status", "--porcelain") == "":
        head = gitops.head_sha(repo)
        if head != base_sha:
            return head
        raise MnemeError(f"nothing to commit for this {kind} pass")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"knowledge: {kind} {date}"
    body = "\n".join(f"- {line}" for line in unit_lines)
    message = f"{subject}\n\n{body}\n\nMneme-{kind.capitalize()}: {plugin}\n"
    gitops.git(repo, "commit", "-m", message)
    return gitops.head_sha(repo)


def _finalize(
    home: Path, cwd: Path, kind: str, *, push: bool = True,
    retire: list[str] | None = None,
) -> harvest.HarvestResult:
    """Migrate, regenerate, gate, commit, and offer the branch's work as a PR.

    The rollback and index-regeneration behaviour is deliberately the harvest's own
    (`harvest._abort` / `harvest._regenerate_index`) rather than a second implementation:
    both paths write the same repos under the same PR-only doctrine, and a classify that
    rolled back differently from a harvest would be a second set of edge cases to trust.
    """
    scope, repo = resolve(home, cwd)
    branch = gitops.current_branch(repo)
    if not branch.startswith(_branch_prefix(kind)):
        raise MnemeError(
            f"not on a {kind} branch — run 'mneme {kind} begin' before finalizing"
        )
    # main is only ever read: the rail's branch is the whole deliverable (spec §7.3).
    base_sha = gitops.git(repo, "rev-parse", "main")
    result = harvest.HarvestResult(target=scope.name, branch=branch)

    # Raised OUTSIDE the guarded block on purpose: nothing has been changed yet, so the
    # branch — and the work on it — survives for the user to fix and finalize again.
    #
    # Only for the rails an AGENT drives. `_legacy_conflicts` refuses a collision because
    # the pass that just manufactured it can fix it in one edit; the migrate rail has no
    # such author to ask — the user's whole request was "migrate this repo", and the
    # collision is committed history rather than an edit made seconds ago. So it takes the
    # merge, exactly like the harvest, which is never lossy (Task 2's topic-key dedup).
    if kind != "migrate":
        conflicts = _legacy_conflicts(repo)
        if conflicts:
            raise _legacy_conflict_error(kind, conflicts)

    # Validated BEFORE the guarded block, alongside `_legacy_conflicts` and for the same
    # reason: nothing has been changed yet, so a rejection leaves the branch — and the
    # librarian's committed work on it — intact for them to correct and finalize again.
    # Inside the guard, a single mistyped unit id ran `_abort`: reset --hard, checkout main,
    # branch -D. A week of reorganisation destroyed by a typo, with an error message
    # inviting a retry that was no longer possible.
    #
    # Every check reads `main` and the tree as the librarian left it, and the migration
    # below moves fact files without changing a bullet's text or its stem, so nothing here
    # needs the post-migration state.
    main_facts: dict[str, list[str]] = {}
    for rel, text in _main_fact_bullets(repo):
        main_facts.setdefault(units.fact_unit_id(Path(rel).stem, text), []).append(text)
    retired_texts, retired_lines = _accept_retirements(
        repo, _parse_retirements(retire), main_facts
    )
    # Checked HERE, not where the body is assembled: that point is past the guarded block,
    # so raising there left a half-migrated branch with no rollback. It is knowable now —
    # the declarations are already parsed — and it is the same class as every other
    # declaration refusal, which leaves the librarian's work intact to correct.
    retired_section = [f"Retired: {line}" for line in retired_lines]
    if layout.body_length(retired_section) > layout._BODY_MAX:
        raise MnemeError(
            f"{kind}: {len(retired_section)} retirements do not fit in one pull request"
            " body, and a retirement that is not reported is a fact deleted in silence —"
            " split this pass into smaller ones"
        )

    try:
        dirty = not gitops.is_clean(repo)
        ahead = gitops.head_sha(repo) != base_sha
        # The one migration, shared with `harvest.apply_batch` and `mneme migrate`: a rail
        # carrying its own walk would drift from the containment proofs, symlink refusals
        # and never-delete-knowledge merges that only the shared one is tested for.
        migration = layout.migrate_legacy_facts(repo)
        if not (dirty or ahead or migration.lines or migration.removed_dir):
            raise MnemeError(_nothing_to_do(kind))
        harvest._regenerate_index(repo)
        issues = lint.lint_repo(repo)
        if lint.has_errors(issues):
            details = "; ".join(f"{i.code} {i.message}" for i in issues if i.severity == "error")
            raise MnemeError(f"{kind} fails repo lint: {details}")
        changed = _changed_files(repo)
        _scan_gate(repo, changed, kind)
        _preservation_gate(repo, changed, kind, retired_texts)
    except MnemeError:
        harvest._abort(repo, branch, base_sha)
        raise
    except Exception as e:
        harvest._abort(repo, branch, base_sha)
        raise MnemeError(f"{kind} aborted — {type(e).__name__}: {e}") from e

    notes = migration.body()
    # Every changed path is repo CONTENT — a filename a contributor, or a merged pull
    # request, committed — and this list becomes the commit body, the pull request body and
    # the ledger record, on a rail (`mneme migrate`) with no agent anywhere in it. A
    # newline is legal in a filename, so a raw path spliced in here writes lines of its
    # own: `facts/deploys\nMneme-Review: approved by security\n- forged: …\nx.md` renders a
    # forged trailer and an invented finding under the real ones. `_safe` is the same
    # collapse-and-cap every migration note already goes through, which is also what makes
    # the de-duplication below work at all: it compares against notes that were built from
    # `_safe`d paths, so an unsafed path never matched and the file was reported twice.
    #
    # Then the WHOLE list is bounded, not just the notes. `body()` holds the migration's
    # own lines inside a budget precisely because a body past ~65 KB is one `open_pr`
    # quietly declines to open — and appending one line per changed path after it walked
    # straight back off that cliff: 117 KB for a 320-topic repo, with the notes inside
    # their 50 KB all along. One body, one bound.
    reported = [layout._safe(rel) for rel in changed]
    # Retirements lead: they are the only lines that describe knowledge LEAVING the repo,
    # and a reviewer skimming a forty-line body must meet them before the moves. Already
    # `_safe`d by `_accept_retirements`, and inside the same one bound as everything else.
    # Retirements lead AND are never dropped. `bound_body` truncates from the end, so
    # ordering alone was not enough: 400 declarations pushed past the budget and the
    # overflow line silently swallowed 148 of them — from the commit body, the pull request
    # body and the ledger record, which stores this same bounded list. The one line saying
    # knowledge left is the one line that must survive, so the budget is spent on
    # retirements first and the pass is refused outright if they alone cannot fit.
    result.units = retired_section + layout.bound_body(
        notes + [rel for rel in reported if not _named_in(rel, notes)],
        noun="change",
        budget=layout._BODY_MAX - layout.body_length(retired_section),
    )

    try:
        result.commit = _commit(repo, scope.name, kind, result.units, base_sha)
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if push and gitops.has_remote(repo):
            gitops.push_branch(repo, branch)
            title = f"knowledge: {kind} {date} ({len(result.units)} changes)"
            result.pr = gitops.open_pr(repo, branch, title, "\n".join(result.units))
        elif not gitops.has_remote(repo):
            result.pr = "no remote — branch left local; merge it or add a remote and push"
        else:
            result.pr = "push skipped (--no-push) — branch left local"
        # The work is handed over, never merged: back to an untouched main.
        gitops.git(repo, "checkout", "main")
    except Exception as e:
        harvest._abort(repo, branch, base_sha)
        raise MnemeError(
            f"{kind} rolled back after the validation gate — {type(e).__name__}: {e};"
            " the repo is back on a clean main"
        ) from e

    record = {
        "kind": kind,
        "target": scope.name,
        "branch": branch,
        "commit": result.commit,
        "pr": result.pr,
        "units": result.units,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        paths.ensure_layout(home)
        with paths.submitted_path(home).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        # The knowledge is committed and the branch is pushed — a ledger write that
        # fails must not turn a delivered pass into an error the user has to undo.
        pass
    return result


def finalize(
    home: Path, cwd: Path, *, push: bool = True, retire: list[str] | None = None
) -> harvest.HarvestResult:
    """Deliver the librarian's reorganization as a pull request.

    `retire` carries `<retired-unit-id>=<covering-unit-id>` declarations: the only way a
    fact leaves the repo, and one that names what covers it in the pull request.
    """
    return _finalize(home, cwd, "classify", push=push, retire=retire)


def review_finalize(
    home: Path, cwd: Path, *, push: bool = True, retire: list[str] | None = None
) -> harvest.HarvestResult:
    """Deliver facts extracted from inbound pull requests as mneme's own pull request."""
    return _finalize(home, cwd, "review", push=push, retire=retire)


def migrate(home: Path, cwd: Path, *, push: bool = True) -> harvest.HarvestResult:
    """Deliver a legacy layout's migration as a pull request, for a repo with nothing else.

    Every other flow migrates on the way past, which covers every repo that still has
    something to contribute. This is the rail for the repo that is only OLD — nothing
    staged, nothing to classify — where "it will be migrated on the next contribution"
    means never.

    One call, not a begin/finalize pair, because the pair exists to hold a branch open
    while an agent reads a bundle and a human approves a mapping. Here there is nothing to
    approve: the move is deterministic and the human's gate is the pull request, same as
    always. That also makes it atomic — `_finalize` rolls its own branch back on every
    failure path, so a migrate that does not finish leaves no branch and a clean `main`.
    """
    _begin(home, cwd, "migrate")
    return _finalize(home, cwd, "migrate", push=push)
