import json

from mneme_core import gitops, paths, scaffold, staging
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def seed(tmp_path, capsys, name="acme-knowledge"):
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo")
    props = {
        "proposals": [
            {
                "type": "skill", "edit": "new", "target": name,
                "name": "deploy-widget", "description": "Use when deploying the widget service",
                "procedure": "1. preflight\n2. cutover",
                "failure_pattern": "restart loop hits the LB cache",
                "confidence": 0.9, "rationale": "verified",
            },
            {
                "type": "fact", "edit": "new", "target": name, "topic": "staging-env",
                "category": "constraint", "text": "Staging DB resets nightly at 04:00 UTC",
                "tags": ["staging"], "confidence": 0.8, "rationale": "observed",
            },
        ]
    }
    p = tmp_path / "props.json"
    p.write_text(json.dumps(props), encoding="utf-8")
    run(capsys, "--home", str(home), "distill", "ingest", str(p), "--source", "demo@s1")
    return home, target


def test_apply_dry_run_touches_nothing(tmp_path, capsys):
    home, target = seed(tmp_path, capsys)
    ids = ",".join(c.id for c in staging.load_candidates(home))
    code, out, _ = run(
        capsys, "--home", str(home), "share", "apply", "--ids", ids, "--dry-run"
    )
    assert code == 0
    assert out.count("would apply") == 2
    assert len(staging.load_candidates(home)) == 2
    assert not (target / "skills" / "deploy-widget").exists()


def test_apply_end_to_end_lands_on_a_harvest_branch(tmp_path, capsys):
    home, target = seed(tmp_path, capsys, name="personal-kb")
    main_before = gitops.git(target, "rev-parse", "main")
    ids = ",".join(c.id for c in staging.load_candidates(home))
    code, out, _ = run(capsys, "--home", str(home), "share", "apply", "--ids", ids)
    assert code == 0
    assert "harvested personal-kb: 2 units on mneme/harvest-" in out
    assert "pr:" in out
    branch = out.split(" units on ")[1].splitlines()[0].strip()
    # The units are reachable on the branch, and only there: main did not move and the
    # working tree (back on main) is untouched.
    log = gitops.git(target, "log", branch, "-1", "--format=%B")
    assert log.splitlines()[0].startswith("knowledge: harvest")
    assert "skills/deploy-widget/SKILL.md" in gitops.git(
        target, "ls-tree", "-r", "--name-only", branch
    )
    assert gitops.git(target, "rev-parse", "main") == main_before
    assert gitops.current_branch(target) == "main"
    assert not (target / "skills" / "deploy-widget" / "SKILL.md").exists()
    assert staging.load_candidates(home) == []
    assert paths.submitted_path(home).exists()


def test_apply_unknown_id(tmp_path, capsys):
    home, _ = seed(tmp_path, capsys)
    code, _, err = run(capsys, "--home", str(home), "share", "apply", "--ids", "nope")
    assert code == 1
    assert "mneme:" in err


def test_decline_records_and_removes(tmp_path, capsys):
    home, _ = seed(tmp_path, capsys)
    cand = staging.load_candidates(home)[0]
    code, out, _ = run(
        capsys, "--home", str(home), "decline", cand.id, "--reason", "not durable"
    )
    assert code == 0
    assert f"declined {cand.id}" in out
    assert staging.is_declined(home, cand.body)
    assert len(staging.load_candidates(home)) == 1
