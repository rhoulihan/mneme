"""Knowledge unit formats: frontmatter, unit ids, fact bullets (spec §5)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import MnemeError

_FM_DELIM = "---"
_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_NESTED_RE = re.compile(r"^\s+([A-Za-z0-9_-]+):\s*(.*)$")
_VALID_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# `\Z` (end of string), never `$`: `$` also matches before a single trailing newline,
# so `"deploy-widget\n"` would pass as kebab-case and then break every consumer that
# treats the name as a path segment or a frontmatter scalar.
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\Z")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FM_DELIM:
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FM_DELIM:
            end = i
            break
    if end is None:
        raise MnemeError("unterminated frontmatter block")
    meta = _parse_block(lines[1:end])
    body_lines = lines[end + 1 :]
    body = "\n".join(body_lines)
    if text.endswith("\n") and body:
        body += "\n"
    return meta, body


_UNESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}


def _unescape(v: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v) and v[i + 1] in _UNESCAPES:
            out.append(_UNESCAPES[v[i + 1]])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _strip_quotes(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1]:
        # Double quotes carry backslash escapes; single quotes are literal (YAML-ish).
        if v[0] == '"':
            return _unescape(v[1:-1])
        if v[0] == "'":
            return v[1:-1]
    return v


def _collect_indented(lines: list[str], start: int) -> tuple[list[str], int]:
    block: list[str] = []
    i = start
    while i < len(lines) and (lines[i].startswith(" ") or not lines[i].strip()):
        if lines[i].strip():
            block.append(lines[i])
        i += 1
    return block, i


def _parse_block(lines: list[str]) -> dict:
    meta: dict = {}
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw.startswith(" "):
            raise MnemeError(f"unexpected indentation in frontmatter: {raw!r}")
        m = _KEY_RE.match(raw)
        if not m:
            raise MnemeError(f"cannot parse frontmatter line: {raw!r}")
        key, val = m.group(1), m.group(2).strip()
        if val in (">", "|"):
            block, i = _collect_indented(lines, i + 1)
            joiner = " " if val == ">" else "\n"
            meta[key] = joiner.join(s.strip() for s in block).strip()
        elif val:
            meta[key] = _strip_quotes(val)
            i += 1
        else:
            block, i = _collect_indented(lines, i + 1)
            if block and block[0].lstrip().startswith("- "):
                meta[key] = [_strip_quotes(s.lstrip()[2:]) for s in block]
            elif block:
                sub: dict = {}
                for s in block:
                    sm = _NESTED_RE.match(s)
                    if not sm:
                        raise MnemeError(f"cannot parse nested frontmatter line: {s!r}")
                    sub[sm.group(1)] = _strip_quotes(sm.group(2))
                meta[key] = sub
            else:
                meta[key] = ""
    return meta


def _escape(v: str) -> str:
    v = v.replace("\\", "\\\\").replace('"', '\\"')
    return v.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def _quote_if_needed(v: str) -> str:
    # Any value that is not a clean single-line plain scalar is emitted double-quoted
    # with escapes. Notably this covers embedded newlines: writing them raw would let a
    # value's continuation lines masquerade as top-level frontmatter keys on read-back.
    if (
        v == ""
        or v != v.strip()
        or ":" in v
        or v[:1] in ("'", '"', ">", "|", "-", "#")
        or any(c in v for c in "\n\r\t\\\"")
    ):
        return '"' + _escape(v) + '"'
    return v


def _serializable_key(key: object) -> str:
    if not isinstance(key, str) or not _VALID_KEY_RE.match(key):
        raise MnemeError(f"frontmatter key is not serializable: {key!r}")
    return key


def serialize_frontmatter(meta: dict, body: str) -> str:
    out = [_FM_DELIM]
    for key, val in meta.items():
        key = _serializable_key(key)
        if isinstance(val, dict):
            out.append(f"{key}:")
            for k, v in val.items():
                out.append(f"  {_serializable_key(k)}: {_quote_if_needed(str(v))}")
        elif isinstance(val, list):
            out.append(f"{key}:")
            for v in val:
                out.append(f"  - {_quote_if_needed(str(v))}")
        else:
            out.append(f"{key}: {_quote_if_needed(str(val))}")
    out.append(_FM_DELIM)
    return "\n".join(out) + "\n" + body


FACT_CATEGORIES = frozenset({"decision", "constraint", "gotcha", "runbook-note", "reference"})

# A bullet is parsed as a linear head match plus two right-to-left tail walks, NOT as one
# regex. The single pattern this replaces paired a lazy `(?P<text>.+?)` with a repeated
# `(?:\s+#[\w-]+)*` group, which backtracks quadratically: measured on a bullet carrying
# k trailing `#tag`s, 20 KB took 1.2 s and 160 KB took 76 s — and bullet lines arrive from
# pull-request diffs, where a contributor picks the length. Everything below is O(len).
_BULLET_HEAD_RE = re.compile(r"^- \[(?P<category>[a-z-]+)\]\s+(?P<rest>\S.*)$")
_TAG_TOKEN_RE = re.compile(r"#[\w-]+\Z")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_VERIFIED_OPEN = "(verified:"


def normalize_topic_key(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    if words:
        return "-".join(words[:6])
    return content_hash(text)[:8]


@dataclass
class FactBullet:
    category: str
    text: str
    tags: list[str]
    verified: str | None
    line_no: int

    @property
    def topic_key(self) -> str:
        return normalize_topic_key(self.text)


def _split_verified(rest: str) -> tuple[str, str | None]:
    """Peel a trailing `(verified: YYYY-MM-DD)` stamp off the bullet's tail.

    Index arithmetic rather than a search: `\\s+\\(verified:` scanned right-to-left over a
    long run of whitespace is itself quadratic, which is the class of bug this replaces.
    """
    if not rest.endswith(")"):
        return rest, None
    open_at = rest.rfind(_VERIFIED_OPEN)
    # The stamp must be preceded by whitespace and follow at least one character of text.
    if open_at <= 0 or not rest[open_at - 1].isspace():
        return rest, None
    inner = rest[open_at + len(_VERIFIED_OPEN) : -1]
    date = inner.lstrip()
    if not _ISO_DATE_RE.fullmatch(date):
        return rest, None
    return rest[:open_at].rstrip(), date


def _split_tags(rest: str) -> tuple[str, list[str]]:
    """Peel trailing `#tag` tokens off the bullet's tail, right to left."""
    tags: list[str] = []
    end = len(rest)
    while True:
        stop = end
        while stop > 0 and rest[stop - 1].isspace():
            stop -= 1
        start = stop
        while start > 0 and not rest[start - 1].isspace():
            start -= 1
        # `start == 0` means the token IS the whole remaining text: a bullet is text with
        # tags after it, never tags alone, so that token stays text.
        if start == 0 or start == stop:
            break
        if not _TAG_TOKEN_RE.fullmatch(rest[start:stop]):
            break
        tags.append(rest[start + 1 : stop])
        end = start
    tags.reverse()
    return rest[:end].strip(), tags


def parse_bullet_line(line: str, line_no: int) -> FactBullet:
    m = _BULLET_HEAD_RE.match(line.rstrip())
    if m:
        rest, verified = _split_verified(m.group("rest").rstrip())
        text, tags = _split_tags(rest)
    if not m or not text:
        raise MnemeError(f"malformed fact bullet at line {line_no}: {line!r}")
    return FactBullet(
        category=m.group("category"),
        text=text,
        tags=tags,
        verified=verified,
        line_no=line_no,
    )


def parse_fact_bullets(body: str) -> list[FactBullet]:
    bullets: list[FactBullet] = []
    for n, line in enumerate(body.splitlines(), start=1):
        if line.startswith("- ["):
            bullets.append(parse_bullet_line(line, n))
    return bullets


def content_hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


# Stamps mneme itself writes onto a rendered unit to record *when and where* it was
# captured — never what it says. `mneme-captured`/`mneme-last-verified` carry the
# ingest date; `mneme-source` carries the session label.
_STAMP_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<key>mneme-captured|mneme-last-verified|mneme-source):[ \t]*.*$",
    re.MULTILINE,
)
# Only the trailing `(verified: YYYY-MM-DD)` a fact bullet ends with. Anchored to end of
# line so an ISO date inside the fact's own text — which *is* semantic content, e.g. two
# decisions naming different cutover dates — is left alone.
_VERIFIED_STAMP_RE = re.compile(
    r"[ \t]*\(verified:[ \t]*\d{4}-\d{2}-\d{2}\)[ \t]*$", re.MULTILINE
)


def strip_capture_stamps(text: str) -> str:
    """Blank out mneme's own capture stamps, leaving the knowledge itself."""
    text = _STAMP_LINE_RE.sub(lambda m: f"{m['indent']}{m['key']}:", text)
    return _VERIFIED_STAMP_RE.sub("", text)


def semantic_hash(text: str) -> str:
    """Hash of what a unit *says*, independent of when or where it was captured.

    Identity for the declined ledger and duplicate detection must not move with the
    calendar: `content_hash` normalizes whitespace only, so the same knowledge rendered
    on a later day (a fresh `verified:`/`mneme-captured` stamp) would hash differently
    and silently resurface a candidate a human already declined.
    """
    return content_hash(strip_capture_stamps(text))


def fact_text_hash(body: str) -> str | None:
    """Identity of what a fact bullet SAYS, or None when the body is not one bullet.

    `semantic_hash` hashes the rendered line, so it only ignores the `verified:` stamp —
    the `[category]` prefix and the `#tags` are contributor-controlled presentation, and
    hashing them made "declined stays declined" and duplicate detection defeatable by a
    one-character tag edit. This key is the bullet's text alone: retag it, recategorize it,
    restamp it, and the same knowledge still hashes the same.
    """
    candidate = body.strip()
    if not candidate.startswith("- ["):
        return None
    try:
        bullet = parse_bullet_line(candidate, 1)
    except MnemeError:
        return None
    return content_hash(bullet.text)


def skill_unit_id(skill_name: str) -> str:
    return f"skills/{skill_name}"


def fact_unit_id(topic_file_stem: str, bullet_text: str) -> str:
    return f"facts/{topic_file_stem}#{normalize_topic_key(bullet_text)}"


# Facts live *inside* the router skill, so the index and the files it routes to travel as
# one self-contained directory. Repos scaffolded before this change keep a top-level
# `facts/`; both layouts stay readable and unit ids (`facts/<stem>#<key>`) never move with
# the physical path.
FACTS_CANONICAL = "skills/knowledge-index/facts"


def facts_dir(root: Path) -> Path:
    """Resolve where a NEW fact is written: canonical, else legacy, else canonical."""
    canonical = root / FACTS_CANONICAL
    if canonical.is_dir():
        return canonical
    legacy = root / "facts"
    if legacy.is_dir():
        return legacy
    return canonical


def facts_dirs(root: Path) -> list[Path]:
    """Every directory that currently holds facts, canonical first.

    `facts_dir` answers "where does the next fact go" — one directory, so writes never
    fork a repo's layout. Readers must answer a different question: "where IS the
    knowledge". A repo carrying BOTH layouts is ordinary, not exotic — a 0.5 scaffold
    ships the canonical dir, and a contributor can still add a top-level `facts/` file by
    hand — and resolving to one directory there makes real, committed facts invisible to
    lint, verify, index, and search until a classify pass migrates them. Every reader
    sweeps this list instead.
    """
    return [d for d in (root / FACTS_CANONICAL, root / "facts") if d.is_dir()]


def fact_files(root: Path) -> list[Path]:
    """Every fact file in the repo, canonical layout first, sorted within each layout."""
    return [f for d in facts_dirs(root) for f in sorted(d.glob("*.md"))]


def find_fact_file(root: Path, stem: str) -> Path | None:
    """The existing file for topic `stem`, in whichever layout carries it.

    `stem` arrives from candidate frontmatter (model-generated text), so the join is
    proven contained rather than trusted: a name that escapes its facts directory
    resolves to no file at all instead of reaching a sibling repo.
    """
    for d in facts_dirs(root):
        path = d / f"{stem}.md"
        try:
            if not path.resolve().is_relative_to(d.resolve()):
                continue
        except OSError:
            continue
        if path.is_file():
            return path
    return None
