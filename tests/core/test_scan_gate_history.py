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
