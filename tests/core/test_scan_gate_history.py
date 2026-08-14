"""The secret scan must cover what the PUSH carries, not what the working tree shows.

`_scan_gate` read `repo/rel` off disk for every changed path. What leaves the machine is
the BRANCH — every commit on it — and a librarian may commit their reorganisation as they
go, which `_commit`'s own docstring calls a finished pass rather than an empty one. So a
secret committed on the branch and then removed from the working tree was pushed inside a
commit no gate had ever read: the tip was clean, the history was not, and `git log -S`
finds it in the pushed branch.

Two shapes, one cause. The file deleted afterwards was skipped outright by
`if not path.is_file(): continue` — "deleted or renamed away, nothing left to leak" is
true of the worktree and false of the history.
"""
import subprocess

import pytest

from mneme_core import classify, gitops, scaffold, units
from mneme_core.errors import MnemeError

SECRET = "aws_key = AKIAIOSFODNN7EXAMPLE"
KEPT = "- [gotcha] A fact that stays right here #d (verified: 2026-08-12)\n"


def make_kb(tmp_path, name="scan-kb"):
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo")
    facts = target / units.FACTS_CANONICAL
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "deploys.md").write_text("---\ntopic: deploys\n---\n" + KEPT, encoding="utf-8")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "seed")
    return home, target


def commit_a_secret_on_the_branch(target, skill="leaky"):
    sk = target / "skills" / skill
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        f"---\nname: {skill}\ndescription: A skill\n---\n\n## Procedure\n\n{SECRET}\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "librarian commits their work")
    return sk


def secret_reachable(target, branch):
    """Is the secret in ANY commit the push would carry?"""
    return subprocess.run(
        ["git", "-C", str(target), "log", "-S", "AKIAIOSFODNN7EXAMPLE", "--oneline", branch],
        capture_output=True, text=True,
    ).stdout.strip() != ""


def test_a_secret_committed_then_deleted_is_still_caught(tmp_path):
    home, target = make_kb(tmp_path, "scan-del")
    classify.begin(home, target)
    sk = commit_a_secret_on_the_branch(target)
    (sk / "SKILL.md").unlink()
    sk.rmdir()
    (target / units.FACTS_CANONICAL / "deploys.md").write_text(
        "---\ntopic: deploys\n---\n" + KEPT
        + "- [gotcha] An added note here now #d (verified: 2026-08-12)\n",
        encoding="utf-8",
    )

    with pytest.raises(MnemeError, match="secret scan"):
        classify.finalize(home, target, push=False)


def test_a_secret_committed_then_cleaned_in_the_worktree_is_still_caught(tmp_path):
    home, target = make_kb(tmp_path, "scan-clean")
    classify.begin(home, target)
    sk = commit_a_secret_on_the_branch(target, "leaky2")
    (sk / "SKILL.md").write_text(
        "---\nname: leaky2\ndescription: A skill\n---\n\n## Procedure\n\nclean now.\n",
        encoding="utf-8",
    )

    with pytest.raises(MnemeError, match="secret scan"):
        classify.finalize(home, target, push=False)


def test_the_error_names_the_commit_the_secret_is_in(tmp_path):
    """A refusal a maintainer cannot act on is barely better than none.

    The path alone is not enough when the working tree is clean — they need to know it is
    in the history, and where, or they will look at the file, see nothing, and retry.
    """
    home, target = make_kb(tmp_path, "scan-name")
    classify.begin(home, target)
    commit_a_secret_on_the_branch(target, "leaky3")

    with pytest.raises(MnemeError) as exc:
        classify.finalize(home, target, push=False)

    message = str(exc.value)
    assert "aws-access-key" in message
    assert "skills/leaky3/SKILL.md" in message


def test_a_clean_branch_with_committed_work_still_finalizes(tmp_path):
    """The librarian committing as they go is a supported pass, not a suspicious one."""
    home, target = make_kb(tmp_path, "scan-ok")
    classify.begin(home, target)
    sk = target / "skills" / "tidy"
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        "---\nname: tidy\ndescription: A skill\n---\n\n## Procedure\n\nNothing secret.\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "librarian commits their work")

    result = classify.finalize(home, target, push=False)

    assert result.branch.startswith("mneme/classify-")
    assert not secret_reachable(target, result.branch)


def test_a_secret_added_to_an_EXISTING_file_then_reverted_is_caught(tmp_path):
    """`--diff-filter=AM`, not `A`.

    The other tests all add a NEW file, so narrowing the filter to additions alone left the
    suite green. The likelier shape in a real classify pass is the opposite: the librarian
    is editing skills that already exist, so a secret pasted into one of them and then
    tidied out of the worktree arrives as a MODIFICATION, and dropping M would ship it.
    """
    home, target = make_kb(tmp_path, "scan-mod")
    # a skill that exists on main before the pass starts
    sk = target / "skills" / "existing"
    sk.mkdir(parents=True, exist_ok=True)
    clean = "---\nname: existing\ndescription: A skill\n---\n\n## Procedure\n\nStep one.\n"
    (sk / "SKILL.md").write_text(clean, encoding="utf-8")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "a skill that already exists")

    classify.begin(home, target)
    (sk / "SKILL.md").write_text(clean + f"\n{SECRET}\n", encoding="utf-8")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "librarian edits an existing skill")
    (sk / "SKILL.md").write_text(clean + "\nTidied.\n", encoding="utf-8")  # cleaned in the worktree

    with pytest.raises(MnemeError, match="secret scan"):
        classify.finalize(home, target, push=False)


# --- What the PUSH ships, decoded the way a reader would see it ---------------------
#
# The first version of the branch scan walked `rev-list main..HEAD` and then each commit's
# `diff-tree --diff-filter=AM`. Right idea, wrong set. Five ways a secret still reached the
# delivered branch, each reproduced end to end before this was rewritten.


def test_a_secret_only_in_a_merge_resolution_is_caught(tmp_path):
    """`diff-tree` prints NOTHING for a merge commit without `-m`/`--cc`.

    So a secret introduced only while resolving a conflict was invisible, and the project's
    own `git log -S` check cannot see it either — that helper skips merges, so the
    regression test was blind to the same class it was written for.
    """
    home, target = make_kb(tmp_path, "scan-merge")
    sk = target / "skills" / "shared"
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        "---\nname: shared\ndescription: A skill\n---\n\n## Procedure\n\nbase.\n", encoding="utf-8"
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "base skill")

    classify.begin(home, target)
    branch = gitops.current_branch(target)
    (sk / "SKILL.md").write_text(
        "---\nname: shared\ndescription: A skill\n---\n\n## Procedure\n\nbranch side.\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "branch edit")
    # a side branch that conflicts, merged in with the secret introduced in the resolution
    gitops.git(target, "checkout", "-q", "main")
    (sk / "SKILL.md").write_text(
        "---\nname: shared\ndescription: A skill\n---\n\n## Procedure\n\nmain side.\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "main edit")
    gitops.git(target, "checkout", "-q", branch)
    subprocess.run(["git", "-C", str(target), "merge", "main"], capture_output=True)
    (sk / "SKILL.md").write_text(
        f"---\nname: shared\ndescription: A skill\n---\n\n## Procedure\n\nresolved.\n{SECRET}\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "resolve the conflict")
    (sk / "SKILL.md").write_text(
        "---\nname: shared\ndescription: A skill\n---\n\n## Procedure\n\nresolved, tidy.\n",
        encoding="utf-8",
    )

    with pytest.raises(MnemeError, match="secret scan"):
        classify.finalize(home, target, push=False)


def test_a_secret_arriving_as_a_type_change_is_caught(tmp_path):
    """`--diff-filter=AM` drops T. A symlink replaced by a real file is a type change.

    Enumerating objects rather than diffs removes the filter question entirely — there is
    no letter to forget.
    """
    home, target = make_kb(tmp_path, "scan-type")
    sk = target / "skills" / "typed"
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        "---\nname: typed\ndescription: A skill\n---\n\n## Procedure\n\nok.\n", encoding="utf-8"
    )
    outside = tmp_path / "outside.md"
    outside.write_text("nothing here\n", encoding="utf-8")
    (sk / "notes.md").symlink_to(outside)
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "a skill with a symlinked note")

    classify.begin(home, target)
    (sk / "notes.md").unlink()
    (sk / "notes.md").write_text(f"# notes\n\n{SECRET}\n", encoding="utf-8")
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "replace the link with a real file")
    (sk / "notes.md").unlink()
    (sk / "notes.md").symlink_to(outside)  # and put the link back, so the tip is innocent

    with pytest.raises(MnemeError, match="secret scan"):
        classify.finalize(home, target, push=False)


def test_a_utf16_secret_is_caught_in_the_worktree_and_in_history(tmp_path):
    """A scanner must not have to guess the encoding correctly.

    BOM-less UTF-16LE decodes "successfully" under `utf-8-sig` into NUL-interleaved
    mojibake, and the rules match nothing in it — so the key leaked at the TIP, with no
    history trick at all. Every plausible decoding is scanned now, and a blocker in any of
    them refuses.
    """
    home, target = make_kb(tmp_path, "scan-utf16")
    sk = target / "skills" / "wide"
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        "---\nname: wide\ndescription: A skill\n---\n\n## Procedure\n\nok.\n", encoding="utf-8"
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "a skill")
    classify.begin(home, target)
    (sk / "notes.md").write_bytes(SECRET.encode("utf-16-le"))

    with pytest.raises(MnemeError, match="secret scan"):
        classify.finalize(home, target, push=False)


def test_a_binary_blob_on_the_branch_does_not_destroy_the_pass(tmp_path):
    """A PNG committed on the branch used to CRASH finalize into `harvest._abort`.

    `git_raw` is text=True, so reading the blob raised UnicodeDecodeError — which is not a
    MnemeError, so it escaped the guard, hard-reset the branch and deleted it, taking the
    librarian's committed work. This repo tracks `assets/mneme.png`; it is not a contrived
    input, and it was a regression against the code this replaced.
    """
    home, target = make_kb(tmp_path, "scan-binary")
    sk = target / "skills" / "arted"
    sk.mkdir(parents=True, exist_ok=True)
    classify.begin(home, target)
    (sk / "SKILL.md").write_text(
        "---\nname: arted\ndescription: A skill\n---\n\n## Procedure\n\nok.\n", encoding="utf-8"
    )
    (sk / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8)
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "librarian commits an image")

    result = classify.finalize(home, target, push=False)

    assert result.branch.startswith("mneme/classify-")
    assert gitops.current_branch(target) == "main"


def test_the_scan_costs_two_git_calls_however_long_the_branch(tmp_path, monkeypatch):
    """Cost is a correctness property here, not tuning.

    The per-commit walk this replaced spawned 1 + commits + commits x files subprocesses —
    1051 for a 50-commit pass, measured at 80 seconds on a drvfs mount, which is long
    enough that a librarian would kill it and lose the pass. Enumerating objects is two
    calls no matter how long the branch: `rev-list --objects`, then one `cat-file --batch`.
    """
    from mneme_core import gitops as g

    home, target = make_kb(tmp_path, "scan-cost")
    classify.begin(home, target)
    sk = target / "skills" / "big"
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        "---\nname: big\ndescription: A skill\n---\n\n## Procedure\n\nx.\n", encoding="utf-8"
    )
    for commit in range(6):
        for i in range(4):
            (sk / f"n{commit}-{i}.md").write_text(f"# {commit}-{i}\n\nfiller\n", encoding="utf-8")
        gitops.git(target, "add", "-A")
        gitops.git(target, "commit", "-m", f"commit {commit}")

    calls = []
    real = g.git_bytes
    monkeypatch.setattr(g, "git_bytes", lambda repo, *a, **kw: (calls.append(a[0]), real(repo, *a, **kw))[1])

    blobs = classify._branch_blobs(target)

    assert len(calls) == 2, calls
    assert calls == ["rev-list", "cat-file"]
    assert len(blobs) >= 24  # every file of every commit, deduplicated by object id


def test_a_secret_on_unpushed_local_main_is_caught(tmp_path):
    """`main..HEAD` is not the range the push ships.

    `push_branch` runs `push -u origin <branch>`, which sends everything the REMOTE does
    not have. When local `main` is ahead of `origin/main`, those commits ride along inside
    the pushed branch — and a range anchored on local `main` never looked at them. The
    range has to be anchored on the remote when there is one.
    """
    home, target = make_kb(tmp_path, "scan-range")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    gitops.git(target, "remote", "add", "origin", str(origin))
    gitops.git(target, "push", "-q", "-u", "origin", "main")

    # a commit on local main that the remote does NOT have, carrying a secret
    sk = target / "skills" / "early"
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        f"---\nname: early\ndescription: A skill\n---\n\n## Procedure\n\n{SECRET}\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "unpushed work on local main")
    (sk / "SKILL.md").write_text(
        "---\nname: early\ndescription: A skill\n---\n\n## Procedure\n\ntidied.\n",
        encoding="utf-8",
    )
    gitops.git(target, "add", "-A")
    gitops.git(target, "commit", "-m", "tidy it on local main")

    classify.begin(home, target)
    (target / units.FACTS_CANONICAL / "deploys.md").write_text(
        "---\ntopic: deploys\n---\n" + KEPT
        + "- [gotcha] An added note here now #d (verified: 2026-08-12)\n",
        encoding="utf-8",
    )

    with pytest.raises(MnemeError, match="secret scan"):
        classify.finalize(home, target, push=False)
