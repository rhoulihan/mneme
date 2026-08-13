"""Deterministic secret/PII scanning for the machine gate (spec §7.2, §8)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

BLOCK = "block"
WARN = "warn"

_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("aws-access-key", BLOCK, re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "github-token",
        BLOCK,
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    ),
    ("slack-token", BLOCK, re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private-key", BLOCK, re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "jwt",
        BLOCK,
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    (
        "assigned-secret",
        BLOCK,
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"]?"
            r"(?P<value>[A-Za-z0-9+/_=-]{12,})"
        ),
    ),
    ("email", WARN, re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
]

_ASSIGN_RE = re.compile(r"[:=]\s*['\"]?(?P<value>[A-Za-z0-9+/_=-]{20,})['\"]?")

# Lowercase words joined by hyphens, and nothing else: no digits, no uppercase, no other
# separator. This is the shape of mneme's own topic slugs (`units.normalize_topic_key`),
# and a fact file's frontmatter reads to `_ASSIGN_RE` as `topic: <value>` — so a slug long
# enough to accumulate distinct characters cleared the 4.0 entropy bar and mneme blocked
# the content mneme had just generated. Observed on a real harvest:
# `mongodb-java-driver-tls-trust-not-configurable-via-uri`, 54 chars, entropy 4.016.
#
# Length is not a usable discriminator, because entropy is not monotonic in it: a
# 60-character slug scores 3.995 and passes while a 45-character one scores 4.047 and
# blocks. Shortening the topic name only hides the problem until the next slug.
#
# The exemption is safe because it is a test of SHAPE, and no credential format has this
# shape — AWS and GitHub tokens carry uppercase, base64 and hex carry digits, and none of
# them use `-` as a word separator. The half that actually does the work is
# `assigned-secret` above: it is keyword-anchored (`password:`, `token:`, `api_key`) and
# never consults entropy, so a secret that announces itself is still caught by its NAME
# no matter how English its value looks. `password: correct-horse-battery-staple` blocks.
_SLUG_RE = re.compile(r"[a-z]+(?:-[a-z]+)+\Z")


@dataclass
class Finding:
    rule: str
    severity: str
    line_no: int
    excerpt: str


def _redact(match_text: str) -> str:
    return (match_text[:4] + "…") if len(match_text) > 4 else "…"


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


def scan_text(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for n, line in enumerate(text.splitlines(), start=1):
        for rule, severity, pattern in _RULES:
            for m in pattern.finditer(line):
                findings.append(Finding(rule, severity, n, _redact(m.group(0))))
        for m in _ASSIGN_RE.finditer(line):
            value = m.group("value")
            if _SLUG_RE.fullmatch(value):
                continue
            if shannon_entropy(value) >= 4.0:
                findings.append(Finding("high-entropy", BLOCK, n, _redact(value)))
    return findings


def has_blockers(findings: list[Finding]) -> bool:
    return any(f.severity == BLOCK for f in findings)
