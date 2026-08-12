"""Regression: the no-push message must not claim 'no remote' when one exists."""
import subprocess

from mneme_core import compose, gitops, harvest, scaffold, staging
from mneme_core.staging import Candidate, candidate_id


def stage_skill(home, target):
    body = compose.render_skill_unit(
        "message-skill", "Use when checking harvest messages", "1. steps",
        "the old message claimed no remote existed", source="s@1", captured="2026-08-12",
    )
    cand = Candidate(
        id=candidate_id("skill", target, body), type="skill", edit="new",
        target=target, body=body, provenance={"source": "s@1", "captured": "2026-08-12"},
    )
    staging.write_candidate(home, cand)
    return cand


def test_no_push_with_remote_names_the_real_reason(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "msg-kb", owner="demo")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    gitops.git(target, "remote", "add", "origin", str(remote))
    gitops.git(target, "push", "-u", "origin", "main")
    cand = stage_skill(home, "msg-kb")
    result = harvest.apply_batch(home, "msg-kb", [cand], push=False)
    assert "push skipped" in result.pr
    assert "no remote" not in result.pr


def test_no_remote_message_unchanged(tmp_path):
    home = tmp_path / "home"
    scaffold.create(home, "local-msg-kb", owner="demo")
    cand = stage_skill(home, "local-msg-kb")
    result = harvest.apply_batch(home, "local-msg-kb", [cand])
    assert "no remote" in result.pr
