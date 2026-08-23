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
    assert mneme_core.__version__ == "0.8.1"
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


# Modules that entered the standard library AFTER this project's floor. Importing one is
# invisible on a developer machine running a newer interpreter and fatal on the floor —
# `tomllib` in `scaffold._manifests` took out 44 test modules on 3.10 while 907 tests passed
# locally on 3.12, and it shipped in a release because nothing checked CI after the merge.
# `tests/e2e/test_release.py` had already rejected `tomllib` for this exact reason, in a
# docstring, three days earlier. A rule that lives only in a comment gets re-broken.
_ABOVE_THE_FLOOR = (
    # (what, first version, how it looks in source). Patterns are explicit rather than
    # derived from the name: deriving them matched `walked` for `pathlib.Path.walk` and
    # reported four files that were innocent.
    ("tomllib", "3.11", r"^\s*(?:import\s+tomllib|from\s+tomllib\s+import)\b"),
    ("enum.StrEnum", "3.11", r"\bStrEnum\b"),
    ("hashlib.file_digest", "3.11", r"\.file_digest\("),
    ("asyncio.TaskGroup", "3.11", r"\bTaskGroup\("),
    ("itertools.batched", "3.12", r"\bbatched\("),
    ("pathlib.Path.walk", "3.12", r"\.walk\("),
)


def _floor() -> str:
    """The lowest Python the CI matrix actually runs — the real contract."""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    versions = re.findall(r'"(\d+\.\d+)"', ci)
    assert versions, "ci.yml declares no python matrix"
    return min(versions, key=lambda v: tuple(int(p) for p in v.split(".")))


def test_the_runtime_imports_nothing_newer_than_the_supported_floor():
    """The engine is stdlib-only, so an import above the floor has no backport to fall to."""
    floor = tuple(int(p) for p in _floor().split("."))
    offenders = []
    for path in sorted((REPO_ROOT / "core").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for what, added, pattern in _ABOVE_THE_FLOOR:
            if tuple(int(p) for p in added.split(".")) <= floor:
                continue
            if re.search(pattern, text, re.M):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}: {what} needs {added}"
                )
    assert not offenders, (
        f"the CI matrix runs {_floor()} and these need newer:\n  " + "\n  ".join(offenders)
    )


def test_the_declared_floor_matches_the_ci_matrix():
    """`requires-python` and the matrix must agree, or one of them is decoration."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'requires-python\s*=\s*["\']>=\s*(\d+\.\d+)["\']', text)
    assert m, "pyproject.toml declares no requires-python floor"
    assert m.group(1) == _floor(), (
        f"pyproject says >={m.group(1)} but CI's lowest matrix entry is {_floor()}"
    )
