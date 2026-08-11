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
            if shannon_entropy(value) >= 4.0:
                findings.append(Finding("high-entropy", BLOCK, n, _redact(value)))
    return findings


def has_blockers(findings: list[Finding]) -> bool:
    return any(f.severity == BLOCK for f in findings)
