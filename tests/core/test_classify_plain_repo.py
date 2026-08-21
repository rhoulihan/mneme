"""`classify` declines in a plain repo. `review`, `share` and `migrate` do not.

The librarian pass exists to file loose facts INTO destination skills. A plain repo has no
destination skills — mneme keeps to `mneme-index/`, and the repo's own `skills/` (if it has
one at all) belongs to the application. So classify there has nowhere to put anything: it
would open a branch, bundle a set of facts, find zero destinations, and either do nothing
or start filing knowledge into somebody's source tree.

Declining is not a limitation to apologise for — it is the whole reason the other rails are
safe to run on a repo mneme does not own. The refusal names what does work, because a user
who reads "not supported" and nothing else has no idea they can still capture and ship.
"""
import pytest

from mneme_core import classify, gitops, harvest, layout, scaffold
from mneme_core.errors import MnemeError

from tests.core.test_plain_repo_harvest import plain_repo, stage_fact


def adopted_app(tmp_path):
    home, repo = plain_repo(tmp_path)
    scaffold.adopt(home, "payments-service")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "chore: adopt mneme")
    return home, repo


def test_classify_declines_and_says_what_does_work(tmp_path):
    home, repo = adopted_app(tmp_path)
    with pytest.raises(MnemeError) as e:
        classify.begin(home, repo)
    message = str(e.value)
    assert "destination" in message
    assert "review" in message and "share" in message
    # And it declined BEFORE touching the repo: no branch, still on main.
    assert gitops.current_branch(repo) == "main"
    # `branch --list <pattern>` returns "" on no match, so asserting absence in it is
    # near-vacuous. Ask for every branch and check the pattern against the real list.
    assert not [
        b for b in gitops.git(repo, "branch", "--list").splitlines()
        if "mneme/classify-" in b
    ]


def test_classify_declines_at_every_door_not_only_the_first(tmp_path):
    """A refusal only in `begin` is a refusal an agent routes around."""
    home, repo = adopted_app(tmp_path)
    for call in (classify.bundle, classify.finalize, classify.begin):
        with pytest.raises(MnemeError, match="destination"):
            call(home, repo)


def test_review_still_works_on_a_plain_repo(tmp_path):
    """The one rail Rick asked for: accept a pull request and the facts inside it."""
    home, repo = adopted_app(tmp_path)
    branch = classify.review_begin(home, repo)
    assert branch.startswith("mneme/review-")
    assert gitops.current_branch(repo) == branch
    classify.review_abort(home, repo)
    assert gitops.current_branch(repo) == "main"


def test_share_still_works_on_a_plain_repo(tmp_path):
    home, repo = adopted_app(tmp_path)
    main_before = gitops.git(repo, "rev-parse", "main")
    result = harvest.apply_batch(
        home, "payments-service", [stage_fact(home, "payments-service")], push=False
    )
    assert gitops.git(repo, "rev-parse", "main") == main_before
    assert "mneme-index/facts/refunds.md" in gitops.git(
        repo, "ls-tree", "-r", "--name-only", result.branch
    ).splitlines()


def test_migrate_still_works_on_a_plain_repo(tmp_path):
    """A plain repo can carry a pre-0.5 `facts/` too, and moving it needs no destinations."""
    home, repo = adopted_app(tmp_path)
    legacy = repo / "facts"
    legacy.mkdir()
    (legacy / "deploys.md").write_text(
        "---\ntopic: deploys\n---\n"
        "- [gotcha] The drain window is ninety seconds #deploy (verified: 2026-08-14)\n",
        encoding="utf-8",
    )
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "a legacy fact")
    gitops.create_branch(repo, "mneme/migrate-test")

    result = layout.migrate_legacy_facts(repo)

    assert result.moved
    assert (repo / "mneme-index" / "facts" / "deploys.md").is_file()


def test_classify_is_unaffected_in_a_plugin_repo(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    branch = classify.begin(home, target)
    assert branch.startswith("mneme/classify-")
    classify.abort(home, target)
