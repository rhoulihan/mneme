"""Fact applies are *delta* edits: exactly one bullet line changes, byte-for-byte.

Regression cover for adopted/hand-authored repos, where a fact file's formatting is not
mneme's own output — CRLF line endings, a BOM, frontmatter comments, legacy bullets.
"""
import subprocess

from mneme_core import compose, gitops, harvest, scaffold, staging
from mneme_core.staging import Candidate, candidate_id

BULLET_A = (
    "- [constraint] Staging DB resets nightly at 04:00 UTC #staging (verified: 2026-08-11)"
)
BULLET_B = (
    "- [gotcha] v2 API truncates batch writes over 500 items #api (verified: 2026-08-11)"
)
KEY_A = "staging-db-resets-nightly-at-04"


def crlf_file(path, extra_frontmatter=""):
    lines = ["---", "topic: staging-env"]
    if extra_frontmatter:
        lines.extend(extra_frontmatter.splitlines())
    lines.extend(["---", BULLET_A, BULLET_B])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    return path


def make_candidate(body, topic="staging-env", edit="new", target_unit="", target="acme-knowledge"):
    return Candidate(
        id=candidate_id("fact", target, body),
        type="fact", edit=edit, target=target,
        body=body, topic=topic, target_unit=target_unit,
    )


def update_candidate(text="Staging DB resets nightly at 03:00 UTC now", **kw):
    body = compose.render_fact_bullet(
        "constraint", text, ["staging"], verified="2026-08-12"
    )
    return make_candidate(
        body, edit="update", target_unit=f"facts/staging-env#{KEY_A}", **kw
    )


def keepends(path):
    return path.read_bytes().decode("utf-8").splitlines(keepends=True)


def test_update_touches_exactly_one_line_of_a_crlf_file(tmp_path):
    path = crlf_file(tmp_path / "facts" / "staging-env.md")
    before = keepends(path)

    harvest.apply_fact(tmp_path, update_candidate())

    after = keepends(path)
    assert len(after) == len(before)
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert changed == [3]  # the one bullet, nothing else
    assert after[3].endswith("\r\n")  # the file's own line ending survives
    assert "03:00 UTC now" in after[3]


def test_update_preserves_frontmatter_comments_and_unknown_keys(tmp_path):
    path = crlf_file(
        tmp_path / "facts" / "staging-env.md",
        extra_frontmatter="# hand-written: owned by the platform team\nowner: platform",
    )
    before = keepends(path)

    harvest.apply_fact(tmp_path, update_candidate())

    after = keepends(path)
    assert "# hand-written: owned by the platform team\r\n" in after
    assert "owner: platform\r\n" in after
    assert [i for i, (a, b) in enumerate(zip(before, after)) if a != b] == [5]


def test_update_preserves_a_byte_order_mark(tmp_path):
    path = crlf_file(tmp_path / "facts" / "staging-env.md")
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    harvest.apply_fact(tmp_path, update_candidate())

    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert data.count(b"\xef\xbb\xbf") == 1
    assert b"03:00 UTC now" in data


def test_append_adds_one_line_in_the_files_own_line_ending(tmp_path):
    path = crlf_file(tmp_path / "facts" / "staging-env.md")
    before = keepends(path)
    new = compose.render_fact_bullet(
        "runbook-note", "Restore staging from the 05:00 snapshot", ["staging"],
        verified="2026-08-12",
    )

    harvest.apply_fact(tmp_path, make_candidate(new))

    after = keepends(path)
    assert after[: len(before)] == before  # every existing byte untouched
    assert len(after) == len(before) + 1
    assert after[-1] == new + "\r\n"


def test_append_tolerates_a_malformed_neighbour_bullet(tmp_path):
    """A legacy unparseable bullet must not make the whole topic un-appendable.

    `edit="update"` has always skipped malformed bullets; the dedup scan on the new-fact
    path must do the same, or one bad line in an adopted repo blocks every future append.
    """
    path = tmp_path / "facts" / "staging-env.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntopic: staging-env\n---\n- [constraint]\n" + BULLET_A + "\n", encoding="utf-8"
    )
    new = compose.render_fact_bullet(
        "gotcha", "v2 API truncates batch writes over 500 items", ["api"],
        verified="2026-08-12",
    )

    line = harvest.apply_fact(tmp_path, make_candidate(new))

    assert line.endswith("(new fact)")
    text = path.read_text(encoding="utf-8")
    assert "- [constraint]\n" in text  # the malformed neighbour is left alone
    assert text.endswith(new + "\n")


def test_update_survives_a_malformed_neighbour_bullet(tmp_path):
    path = tmp_path / "facts" / "staging-env.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntopic: staging-env\n---\n- [constraint]\n" + BULLET_A + "\n", encoding="utf-8"
    )

    harvest.apply_fact(tmp_path, update_candidate())

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[3] == "- [constraint]"
    assert "03:00 UTC now" in lines[4]


def test_harvest_commit_diff_is_one_line_on_a_committed_crlf_file(tmp_path):
    """End-to-end: the harvest commit itself must not rewrite the whole file."""
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo", mode="commit")
    crlf_file(target / "facts" / "staging-env.md")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "chore: hand-authored CRLF fact file")

    cand = update_candidate()
    staging.write_candidate(home, cand)
    harvest.apply_batch(home, "acme-knowledge", [cand], push=False)

    numstat = subprocess.run(
        ["git", "-C", str(target), "diff", "--numstat", "HEAD~1", "HEAD",
         "--", "facts/staging-env.md"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert numstat[:2] == ["1", "1"]  # one line added, one removed — not the whole file
