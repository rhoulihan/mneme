import json
import re
from pathlib import Path

import mneme_core
import mneme_index

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    """Return `[project].version` from pyproject.toml.

    Parsed with a scoped regex rather than tomllib: the supported floor is
    Python 3.10 and tomllib only landed in 3.11, and the runtime is
    stdlib-only so there is no toml backport to fall back on.
    """
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    table = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    assert table is not None, "pyproject.toml has no [project] table"
    match = re.search(
        r"""^version\s*=\s*["']([^"']+)["']\s*$""", table.group(1), re.M
    )
    assert match is not None, "pyproject.toml [project] table declares no version"
    return match.group(1)


def test_version_consistency():
    assert mneme_core.__version__ == "0.7.0"
    manifest = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == mneme_core.__version__
    # The distribution version is what `pip install .` stamps on the wheel, so a
    # stale value here ships 0.2.0 code labelled as some older release.
    assert _pyproject_version() == mneme_core.__version__
    # mneme_index ships inside the same single distribution and is standalone by
    # import boundary, not by release cadence — it is not independently
    # versioned (see the CHANGELOG preamble).
    assert mneme_index.__version__ == mneme_core.__version__
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.lstrip().startswith("# Changelog")
    assert f"## {mneme_core.__version__}" in changelog


def test_readme_status_complete():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "🔨 in progress" not in readme
    assert "📝 planned" not in readme


def _first_changelog_section() -> str:
    """The heading of the newest release section in CHANGELOG.md."""
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = [
        line for line in changelog.splitlines() if line.startswith("## ")
    ]
    assert headings, "CHANGELOG.md has no release sections at all"
    return headings[0]


def test_the_changelog_leads_with_the_version_being_shipped():
    """A release whose notes are filed under an older heading reads as unreleased.

    `test_version_consistency` only asks that the version appear SOMEWHERE in the
    file, which a section appended at the bottom — or a version bumped with the
    notes left under the previous heading — satisfies while every reader of the
    changelog is told the newest release is the one before it.
    """
    heading = _first_changelog_section()
    assert heading.startswith(f"## {mneme_core.__version__}"), heading


def test_the_readme_status_table_records_the_version_being_shipped():
    """The README status table is the release's user-visible index; a bump without a
    row there ships a phase nobody reading the front page knows landed."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    rows = [line for line in readme.splitlines() if line.startswith("|")]
    assert [r for r in rows if f"(v{mneme_core.__version__})" in r], (
        f"no README status row names v{mneme_core.__version__}"
    )
