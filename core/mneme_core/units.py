"""Knowledge unit formats: frontmatter, unit ids, fact bullets (spec §5)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .errors import MnemeError

_FM_DELIM = "---"
_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_NESTED_RE = re.compile(r"^\s+([A-Za-z0-9_-]+):\s*(.*)$")
_VALID_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")

KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


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

_BULLET_RE = re.compile(
    r"^- \[(?P<category>[a-z-]+)\]\s+(?P<text>.+?)"
    r"(?P<tags>(?:\s+#[\w-]+)*)"
    r"(?:\s+\(verified:\s*(?P<verified>\d{4}-\d{2}-\d{2})\))?\s*$"
)


def normalize_topic_key(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words[:6])


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


def parse_bullet_line(line: str, line_no: int) -> FactBullet:
    m = _BULLET_RE.match(line)
    if not m:
        raise MnemeError(f"malformed fact bullet at line {line_no}: {line!r}")
    tags = re.findall(r"#([\w-]+)", m.group("tags") or "")
    return FactBullet(
        category=m.group("category"),
        text=m.group("text").strip(),
        tags=tags,
        verified=m.group("verified"),
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


def skill_unit_id(skill_name: str) -> str:
    return f"skills/{skill_name}"


def fact_unit_id(topic_file_stem: str, bullet_text: str) -> str:
    return f"facts/{topic_file_stem}#{normalize_topic_key(bullet_text)}"
