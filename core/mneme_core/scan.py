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

# mneme writes a fact file's own `topic:` frontmatter, and `_ASSIGN_RE` reads that line as
# an assignment. A slug long enough to accumulate distinct characters clears the 4.0 entropy
# bar, so mneme blocked content mneme had just generated — a real harvest failed CI on
# `mongodb-java-driver-tls-trust-not-configurable-via-uri` (54 chars, entropy 4.016).
# Length is not a usable discriminator: entropy is not monotonic in it (a 60-character slug
# scores 3.995 and passes while a 45-character one scores 4.047 and blocks).
#
# The exemption is deliberately narrow on THREE axes at once, because a wider version of it
# was written first and opened a real hole. It matched any lowercase-hyphenated value under
# any key, on the reasoning that no credential looks like that — which is false. Diceware,
# 1Password's memorable password and Bitwarden's passphrase generator all emit exactly that
# shape by default, and `\b` does not fire across an underscore, so `db_password`,
# `client_secret`, `access_token` and `PGPASSWORD` never reach the keyword-anchored
# `assigned-secret` rule either. Measured: 236 of 280 realistic passphrase assignments went
# from BLOCK to clean. Two survivable gaps composed into an open door.
#
# So the exemption is keyed on the LINE, not the value: it must be one of the frontmatter
# keys mneme itself generates, holding a kebab-case value and nothing else on the line.
# `db_password: …` is not exempt under ANY value, and that key-scoping — not the value
# shape — is what closes the hole.
#
# The word cap is 12 rather than `normalize_topic_key`'s 6: a fact file's `topic:` holds the
# topic NAME, which is only constrained to kebab-case, and the slug that prompted this fix
# is 9 words. It bounds how much can sit on an exempt line without pretending to be a
# semantic check.
#
# Residual risk, stated plainly: a secret shaped like a diceware passphrase would be exempt
# if written under `topic:` or `name:` specifically. That is accepted — those keys hold a
# fact's topic, the value is human-reviewed at the share gate, and the alternative is
# blocking every descriptive topic mneme generates.
_TOPIC_LINE_RE = re.compile(
    r"\A\s*(?:topic|name)\s*:\s*['\"]?[a-z0-9]+(?:-[a-z0-9]+){0,11}['\"]?\s*\Z"
)


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
        if _TOPIC_LINE_RE.match(line):
            continue
        for m in _ASSIGN_RE.finditer(line):
            value = m.group("value")
            if shannon_entropy(value) >= 4.0:
                findings.append(Finding("high-entropy", BLOCK, n, _redact(value)))
    return findings


def has_blockers(findings: list[Finding]) -> bool:
    return any(f.severity == BLOCK for f in findings)
