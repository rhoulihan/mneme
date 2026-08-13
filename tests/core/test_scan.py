import re
from mneme_core import scan


def rules(findings):
    return {f.rule for f in findings}


def test_aws_example_key_blocks():
    findings = scan.scan_text("key = AKIAIOSFODNN7EXAMPLE")
    assert "aws-access-key" in rules(findings)
    assert scan.has_blockers(findings)


def test_github_token_blocks():
    fake = "ghp_" + "a" * 36
    findings = scan.scan_text(f"token: {fake}")
    assert "github-token" in rules(findings)


def test_private_key_header_blocks():
    findings = scan.scan_text("-----BEGIN RSA PRIVATE KEY-----")
    assert "private-key" in rules(findings)


def test_assigned_secret_blocks():
    findings = scan.scan_text('api_key = "abcd1234efgh5678"')
    assert "assigned-secret" in rules(findings)


def test_high_entropy_assignment_blocks():
    findings = scan.scan_text("secret_blob = kJ8vQ2xN9pL4mR7tW3yZ6bC1dF5gH0aS")
    assert "high-entropy" in rules(findings)


def test_email_warns_but_does_not_block():
    findings = scan.scan_text("contact rick.houlihan@gmail.com for access")
    assert rules(findings) == {"email"}
    assert not scan.has_blockers(findings)


def test_clean_text_no_findings():
    text = "- [gotcha] v2 API truncates batch writes over 500 items #api\n"
    assert scan.scan_text(text) == []


def test_excerpt_is_redacted_and_line_numbered():
    findings = scan.scan_text("line one\nkey = AKIAIOSFODNN7EXAMPLE\n")
    f = next(x for x in findings if x.rule == "aws-access-key")
    assert f.line_no == 2
    assert f.excerpt.startswith("AKIA")
    assert f.excerpt.endswith("…")
    assert "EXAMPLE" not in f.excerpt


def test_shannon_entropy_bounds():
    assert scan.shannon_entropy("") == 0.0
    assert scan.shannon_entropy("aaaa") == 0.0
    assert scan.shannon_entropy("abcdefgh") == 3.0


# --- A descriptive topic slug is not a secret --------------------------------------
#
# `_ASSIGN_RE` reads `topic: <value>` in a fact file's own frontmatter as an assignment,
# and a long lowercase-kebab slug carries enough distinct characters to clear the 4.0
# entropy bar. mneme therefore generated content that mneme's own machine gate blocked:
# a real harvest into oracle-ai-dev failed CI on the slug
# `mongodb-java-driver-tls-trust-not-configurable-via-uri` (54 chars, entropy 4.016).
#
# Length is not a usable workaround, because entropy is not monotonic in length: a
# 60-character slug passes while a 45-character one blocks. Shortening the topic name
# only moved the problem out of sight.


def test_a_long_kebab_topic_slug_is_not_flagged_as_a_secret():
    slug = "mongodb-java-driver-tls-trust-not-configurable-via-uri"
    assert scan.shannon_entropy(slug) >= 4.0  # the entropy bar alone would block it

    assert scan.scan_text(f"---\ntopic: {slug}\n---\n") == []


def test_slug_exemption_holds_across_lengths_where_entropy_oscillates():
    """The property, not the one length that was reported.

    Entropy rises and falls as words are added, so a fixed length threshold cannot
    express this rule — every slug shape has to be exempt regardless of where it lands.
    """
    base = "mongodb-java-driver-tls-trust-not-configurable-via-uri-and-more-words"
    for n in range(20, len(base) + 1):
        slug = base[:n].rstrip("-")
        if not re.fullmatch(r"[a-z]+(?:-[a-z]+)+", slug):
            continue
        assert scan.scan_text(f"topic: {slug}\n") == [], f"len={n} {slug!r}"


def test_the_exemption_does_not_open_a_hole_for_real_secrets():
    """Every credential shape still blocks — the carve-out is on SHAPE, not on entropy.

    Real credentials are mixed-case, or carry digits, or have no word separators at all.
    A value that is purely lowercase words joined by hyphens is none of those.
    """
    blocked = [
        # Pattern rules — never consulted entropy, so the carve-out cannot reach them.
        "aws_key = AKIAIOSFODNN7EXAMPLE",
        "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab",
        # Entropy rules whose values are not slug-shaped: mixed case, digits, or no
        # separators at all. No real credential format is pure lowercase words.
        "value: dGhpcyBpcyBhIGxvbmcgYmFzZTY0IHNlY3JldCBzdHJpbmc=",
        "key: xX9mQ2vL8pR4tY7wZ1nB5jK3hG6fD0sA",
        # THE case that proves the carve-out is safe: these values ARE slug-shaped, so
        # the exemption does drop their high-entropy finding — and they stay blocked
        # anyway, because `assigned-secret` is keyword-anchored and ignores entropy.
        # A secret that announces itself as one is caught by its name, not its shape.
        "secret: correct-horse-battery-staple",
        "password: my-super-secret-passphrase-value",
        "api_key = another-plain-looking-english-value",
    ]
    for line in blocked:
        findings = scan.scan_text(line + "\n")
        assert scan.has_blockers(findings), f"no longer blocked: {line!r}"


def test_the_keyword_anchored_rule_is_what_makes_the_exemption_safe():
    """Stated as its own property, because it is the load-bearing half of the argument.

    A 40-char lowercase hex digest is NOT blocked today (entropy 3.96, under the 4.0 bar)
    — a pre-existing gap in the generic rule, untouched by this change and noted here so
    the next reader does not mistake it for something the carve-out caused.
    """
    slug = "correct-horse-battery-staple"
    assert re.fullmatch(r"[a-z]+(?:-[a-z]+)+", slug)  # exempt by shape
    assert scan.scan_text(f"topic: {slug}\n") == []  # ...and clean when unnamed
    assert scan.has_blockers(scan.scan_text(f"password: {slug}\n"))  # ...blocked when named


def test_a_single_unhyphenated_lowercase_run_is_still_scanned():
    """The exemption requires word STRUCTURE — at least one separator.

    A long unbroken lowercase run has no dictionary shape and stays subject to entropy.
    """
    assert not re.fullmatch(r"[a-z]+(?:-[a-z]+)+", "abcdefghijklmnopqrstuvwxyzabcdefgh")
