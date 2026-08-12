import json
from pathlib import Path

import mneme_core

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_plugin_manifest():
    data = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert data["name"] == "mneme"
    assert data["version"] == mneme_core.__version__
    assert data["license"] == "Apache-2.0"
    assert "rhoulihan/mneme" in data["repository"]
    assert data["description"]


def test_marketplace_manifest():
    data = json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert data["name"] == "mneme"
    assert data["owner"]["name"] == "rhoulihan"
    assert data["plugins"][0]["name"] == "mneme"
    assert data["plugins"][0]["source"] == "./"


def test_install_doc_exists_and_covers_basics():
    text = (REPO_ROOT / "docs" / "install.md").read_text(encoding="utf-8")
    for token in (
        "marketplace add rhoulihan/mneme",
        "MNEME_HOME",
        "MNEME_DISTILL_MODEL",
        "distill.log",
    ):
        assert token in text, token
