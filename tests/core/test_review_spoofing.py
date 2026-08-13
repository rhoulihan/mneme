"""PR content must never be readable as PR structure (spec §7.8).

A unified diff renders every added line with a `+` prefix, so a file whose CONTENT is
`++ b/<path>` reaches the parser as `+++ b/<path>` — the exact shape of a file header. A
line-by-line scan therefore let any pull request attribute fabricated fact bullets, and
fabricated skill additions, to files it never touched: the maintainer would be shown
attacker text as an addition to a trusted fact file, and the review skill writes approved
bullets into the facts directory verbatim. The fabricated hashes also entered the cross-PR
`seen` set, so an honest PR proposing that knowledge afterwards was labelled `duplicate` —
a knowledge-SUPPRESSION path, steering the maintainer to close it.

Every diff here is produced by real `git diff`, not hand-assembled: the bug was in how git
renders content, so a synthetic fixture could not prove the fix.
"""
import json
import os
import stat

from mneme_core import gitops, review, scaffold, units

SPOOF_TARGET = "skills/knowledge-index/facts/deploys.md"
SPOOF_FACT = "- [decision] Fabricated by the attacker #ops (verified: 2026-08-12)"
HONEST_FACT = "- [decision] Fabricated by the attacker #ops (verified: 2026-08-13)"


def shim_gh(tmp_path, monkeypatch, pr_list_json, diffs):
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    (bindir / "prlist.json").write_text(pr_list_json, encoding="utf-8")
    for n, diff in diffs.items():
        (bindir / f"diff{n}.txt").write_text(diff, encoding="utf-8")
    gh = bindir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *"pr list"*) cat "{bindir}/prlist.json" ;;\n'
        f'  *"pr diff"*) n=$(echo "$@" | grep -o "[0-9]*" | head -1); cat "{bindir}/diff$n.txt" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def real_diff(tmp_path, name, files, *, base=None):
    """`git diff` over a scratch repo — the renderer whose output the parser must survive."""
    repo = tmp_path / name
    repo.mkdir(parents=True)
    gitops.git(repo, "init", "-q", "-b", "main")
    gitops.git(repo, "config", "user.email", "spoof@example.com")
    gitops.git(repo, "config", "user.name", "spoof")
    for rel, text in (base or {}).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    gitops.git(repo, "add", "-A")
    return gitops.git_raw(repo, "diff", "--cached")


def test_content_shaped_like_a_header_cannot_forge_fact_additions(tmp_path):
    diff = real_diff(
        tmp_path,
        "spoof-facts",
        {"docs/evil.md": f"intro\n++ b/{SPOOF_TARGET}\n{SPOOF_FACT}\n"},
        base={"docs/evil.md": "intro\n"},
    )
    # The rendered diff really does carry the header shape — otherwise this proves nothing.
    assert f"+++ b/{SPOOF_TARGET}" in diff

    facts, skipped = review.parse_added_facts(42, diff)

    assert facts == []
    assert skipped == []


def test_content_shaped_like_a_header_cannot_forge_a_skill_addition(tmp_path):
    diff = real_diff(
        tmp_path,
        "spoof-skills",
        {
            "docs/evil2.md": (
                "new file mode 100644\n"
                "--- /dev/null\n"
                "++ b/skills/totally-legit-skill/SKILL.md\n"
                "---\nname: totally-legit-skill\n"
            )
        },
    )
    assert "+++ b/skills/totally-legit-skill/SKILL.md" in diff

    assert review.parse_added_skills(99, diff) == []


def test_a_real_fact_addition_still_parses(tmp_path):
    """The counterpart guard: hardening the walk must not blind it to genuine additions."""
    diff = real_diff(
        tmp_path,
        "honest",
        {SPOOF_TARGET: f"---\ntopic: deploys\n---\n{SPOOF_FACT}\n"},
    )

    facts, skipped = review.parse_added_facts(7, diff)

    assert [(f.file, f.text) for f in facts] == [
        (SPOOF_TARGET, "Fabricated by the attacker")
    ]
    assert skipped == []


def test_spoofed_facts_never_suppress_an_honest_later_pr(tmp_path, monkeypatch):
    """The consequence that mattered: a forged addition poisoning cross-PR dedup."""
    home = tmp_path / "home"
    target = scaffold.create(home, "spoof-kb", owner="demo")
    (target / units.FACTS_CANONICAL).mkdir(parents=True, exist_ok=True)
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "fixtures", "--allow-empty")

    attacker = real_diff(
        tmp_path,
        "attacker",
        {"docs/evil.md": f"intro\n++ b/{SPOOF_TARGET}\n{SPOOF_FACT}\n"},
        base={"docs/evil.md": "intro\n"},
    )
    honest = real_diff(
        tmp_path,
        "contributor",
        {SPOOF_TARGET: f"---\ntopic: deploys\n---\n{HONEST_FACT}\n"},
    )
    pr_list = json.dumps(
        [
            {"number": 1, "title": "docs tweak", "author": {"login": "mallory"},
             "url": "https://example.com/pr/1"},
            {"number": 2, "title": "deploy knowledge", "author": {"login": "alice"},
             "url": "https://example.com/pr/2"},
        ]
    )
    shim_gh(tmp_path, monkeypatch, pr_list, {1: attacker, 2: honest})

    bundle = review.triage(home, target)

    assert bundle["prs"][0]["facts"] == []
    honest_facts = bundle["prs"][1]["facts"]
    assert [f["status"] for f in honest_facts] == ["new"]
    assert honest_facts[0]["file"] == SPOOF_TARGET
