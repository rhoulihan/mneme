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


# --- A descriptive topic slug is not a secret, but almost everything else is -------
#
# `_ASSIGN_RE` reads a fact file's own `topic:` frontmatter as an assignment, and a long
# lowercase slug clears the 4.0 entropy bar, so mneme blocked content mneme had generated:
# a real harvest failed CI on `mongodb-java-driver-tls-trust-not-configurable-via-uri`
# (54 chars, entropy 4.016). Length cannot express the rule — entropy is not monotonic in
# it, so a 60-character slug passes while a 45-character one blocks.
#
# The FIRST attempt at this exemption matched any lowercase-hyphenated value under any key
# and opened a real hole: diceware / 1Password / Bitwarden passphrases are exactly that
# shape, and `\b` does not fire across `_`, so `db_password` never reaches the
# keyword-anchored rule that was supposed to be the backstop. These tests therefore pin
# the ESCAPE PATH closed as hard as they pin the false positive open, and every value used
# below is asserted to clear the entropy bar first — the earlier versions of these tests
# used values scoring 3.49 and so never exercised the exemption at all.

# Real generator output: lowercase words joined by hyphens, entropy above the 4.0 bar.
PASSPHRASES = [
    "quartz-jumble-vexing-drowsy-flanked",
    "zephyr-quixotic-jumbled-vexing-warmth",
    "banjo-vixen-quartz-glyph-wisdom-flux",
    "cryptic-jawbone-vixen-sludge-morph-quay",
]

# Key names that carry secrets and that `\b(?:...)\b` does NOT reach, because `_` is a
# word character. Before the exemption existed, entropy was the only thing covering them.
UNREACHED_KEYS = [
    "db_password", "client_secret", "access_token",
    "auth_token", "secret_key", "passphrase", "PGPASSWORD",
]


def test_the_reported_topic_slug_no_longer_blocks():
    slug = "mongodb-java-driver-tls-trust-not-configurable-via-uri"
    assert scan.shannon_entropy(slug) >= 4.0  # the bar is genuinely exercised

    assert scan.scan_text(f"---\ntopic: {slug}\n---\n") == []


def test_the_exemption_covers_every_shape_normalize_topic_key_can_emit():
    """A `topic:` value is a topic NAME — kebab-case, and longer than six words in practice.

    Two bounds were wrong in the first attempt at this exemption: it forbade digits, so
    `kubernetes-v1-29-admission-webhook-timeout` still blocked, and a later version capped
    at `normalize_topic_key`'s six words, which does not fix the reported 9-word slug at
    all. Both are pinned here so neither narrowing comes back.
    """
    for slug in [
        "kubernetes-v1-29-admission-webhook-timeout",
        "mongodb-java-driver-tls-trust-not",
        "ora-00933-invalid-sql-statement-here",
        "s3-bucket-policy-denies-cross-account",
    ]:
        assert scan.scan_text(f"topic: {slug}\n") == [], slug


def test_a_passphrase_under_a_secret_key_still_blocks():
    """THE regression this exemption must never reintroduce.

    A wider exemption — any lowercase-hyphenated value, any key — took 236 of 280
    realistic passphrase assignments from BLOCK to clean. These key names are exactly the
    ones the keyword-anchored rule cannot see, so entropy is the only thing standing here.
    """
    for key in UNREACHED_KEYS:
        for value in PASSPHRASES:
            assert scan.shannon_entropy(value) >= 4.0, value
            findings = scan.scan_text(f"{key}: {value}\n")
            assert scan.has_blockers(findings), f"ESCAPED: {key}: {value}"


def test_the_exemption_is_scoped_to_the_line_not_the_value():
    """Same value, different key: exempt only under mneme's own frontmatter keys."""
    value = "quartz-jumble-vexing-drowsy-flanked"
    assert scan.shannon_entropy(value) >= 4.0

    assert scan.scan_text(f"topic: {value}\n") == []
    assert scan.scan_text(f"name: {value}\n") == []
    for key in ("owner", "db_password", "note", "sources"):
        assert scan.has_blockers(scan.scan_text(f"{key}: {value}\n")), key


def test_the_exemption_does_not_survive_extra_content_on_the_line():
    """Anchored end to end, so nothing can ride along beside an exempt slug."""
    value = "quartz-jumble-vexing-drowsy-flanked"
    for line in [
        f"topic: {value} db_password: {value}",
        f"# topic: {value}",
        f"topic: {value} trailing words here",
        # 13 words, one past the cap: no longer exempt, so entropy applies again
        "topic: quartz-jumble-vexing-drowsy-flanked-zephyr-quixotic-warmth-banjo-glyph-wisdom-flux-morph",
    ]:
        assert scan.has_blockers(scan.scan_text(line + "\n")), line


def test_every_other_rule_is_untouched_by_the_exemption():
    for line in [
        "aws_key = AKIAIOSFODNN7EXAMPLE",
        "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab",
        "value: dGhpcyBpcyBhIGxvbmcgYmFzZTY0IHNlY3JldCBzdHJpbmc=",
        "key: xX9mQ2vL8pR4tY7wZ1nB5jK3hG6fD0sA",
        "secret: correct-horse-battery-staple",
        "password: my-super-secret-passphrase-value",
    ]:
        assert scan.has_blockers(scan.scan_text(line + "\n")), line


def test_the_keyword_rule_cannot_see_underscored_names_and_that_is_pre_existing():
    """Recorded as a known gap, NOT introduced here — and not closed here either.

    `\b(?:...|password)\b` does not match inside `db_password`, because `_` is a word
    character. Entropy is what covers those keys, which is precisely why the exemption
    above must never apply to them. Fixing the boundary is its own change; this test
    exists so the next reader knows the gap is understood rather than overlooked.
    """
    low_entropy = "hunter2-hunter2-hunter2"
    assert scan.shannon_entropy(low_entropy) < 4.0

    assert scan.has_blockers(scan.scan_text(f"password: {low_entropy}\n"))  # keyword sees it
    assert scan.scan_text(f"db_password: {low_entropy}\n") == []  # keyword does NOT
