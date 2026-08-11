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
