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


def _first_changelog_section() -> tuple[str, str]:
    """The heading of the newest release section in CHANGELOG.md, and its body.

    The body is every line between that heading and the next one, so a caller
    can tell a release that documents itself from a heading with nothing filed
    under it.
    """
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    lines = changelog.splitlines()
    headings = [i for i, line in enumerate(lines) if line.startswith("## ")]
    assert headings, "CHANGELOG.md has no release sections at all"
    end = headings[1] if len(headings) > 1 else len(lines)
    return lines[headings[0]], "\n".join(lines[headings[0] + 1 : end])


def test_the_changelog_leads_with_the_version_being_shipped():
    """A release whose notes are filed under an older heading reads as unreleased.

    `test_version_consistency` only asks that the version appear SOMEWHERE in the
    file, which a section appended at the bottom — or a version bumped with the
    notes left under the previous heading — satisfies while every reader of the
    changelog is told the newest release is the one before it. The leading
    heading having to carry the notes is half the property: an empty section
    over notes that moved down one heading tells readers this release shipped
    nothing and that its work landed in the release before it.
    """
    heading, body = _first_changelog_section()
    assert heading.startswith(f"## {mneme_core.__version__}"), heading
    assert body.strip(), f"{heading} has no notes under it"
    assert [line for line in body.splitlines() if line.startswith("- ")], (
        f"{heading} lists no changes — every release section in this file "
        "records what it changed as bullets"
    )


def _readme_status_rows() -> list[str]:
    """The rows of the README status table, and nothing else.

    Scoped to the lines between the paragraph that introduces the table and the
    'Deferred by design' paragraph that closes the section. Collecting every
    line in the README that starts with `|` would also sweep in the command
    reference table — and any other table a row could be parked in — so the
    status table could lose a row entirely while a copy of it somewhere further
    down the file kept the check satisfied.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    lines = readme.splitlines()
    opens = [
        i for i, line in enumerate(lines)
        if line.startswith("Mneme is in active development")
    ]
    closes = [
        i for i, line in enumerate(lines)
        if line.startswith("Deferred by design")
    ]
    assert len(opens) == 1, "README has no single status-table lead-in paragraph"
    assert len(closes) == 1, "README has no single 'Deferred by design' paragraph"
    assert opens[0] < closes[0], "README status table is not between its bookends"
    rows = [
        line for line in lines[opens[0] : closes[0]] if line.startswith("|")
    ]
    assert len(rows) > 2, "README status table has a header but no phase rows"
    return rows[2:]


def test_the_readme_status_table_records_the_version_being_shipped():
    """The README status table is the release's user-visible index; a bump without a
    row there ships a phase nobody reading the front page knows landed."""
    version = mneme_core.__version__
    named = [row for row in _readme_status_rows() if f"(v{version})" in row]
    assert named, f"no README status row names v{version}"
    # And it has to claim the phase landed: a row that names the version while
    # disowning it ("reverted", "rolled back") leaves the front page telling
    # readers the release shipped nothing usable, which the ban on the
    # in-progress and planned markers alone does not catch.
    assert [row for row in named if f"✅ merged (v{version})" in row], (
        f"README status row for v{version} does not record it as merged: {named}"
    )
