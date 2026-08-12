from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_contributing_covers_process():
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for token in ("python3 -m pytest", "docs/superpowers/plans", "bin/mneme lint"):
        assert token in text, token


def test_security_covers_reporting_and_scope():
    text = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "security advisor" in text.lower()
    for token in ("secret", "read-only", "hook"):
        assert token in text.lower(), token
