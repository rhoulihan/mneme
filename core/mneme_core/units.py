"""Knowledge unit formats: frontmatter, unit ids, fact bullets (spec §5)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .errors import MnemeError

_FM_DELIM = "---"
_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_NESTED_RE = re.compile(r"^\s+([A-Za-z0-9_-]+):\s*(.*)$")

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


def _strip_quotes(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1].replace('\\"', '"')
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


def _quote_if_needed(v: str) -> str:
    if v == "" or v != v.strip() or ":" in v or v[:1] in ("'", '"', ">", "|", "-", "#"):
        return '"' + v.replace('"', '\\"') + '"'
    return v


def serialize_frontmatter(meta: dict, body: str) -> str:
    out = [_FM_DELIM]
    for key, val in meta.items():
        if isinstance(val, dict):
            out.append(f"{key}:")
            for k, v in val.items():
                out.append(f"  {k}: {_quote_if_needed(str(v))}")
        elif isinstance(val, list):
            out.append(f"{key}:")
            for v in val:
                out.append(f"  - {_quote_if_needed(str(v))}")
        elif isinstance(val, str) and "\n" in val:
            out.append(f"{key}: |")
            for line in val.splitlines():
                out.append(f"  {line}")
        else:
            out.append(f"{key}: {_quote_if_needed(str(val))}")
    out.append(_FM_DELIM)
    return "\n".join(out) + "\n" + body
