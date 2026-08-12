import json

from mneme_core import scaffold, staging
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def seed(tmp_path, capsys, name="acme-knowledge", mode="pr"):
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo", mode=mode)
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


def test_share_list_groups_by_target(tmp_path, capsys):
    home, _ = seed(tmp_path, capsys)
    code, out, _ = run(capsys, "--home", str(home), "share", "list")
    assert code == 0
    assert "acme-knowledge:" in out
    assert "skill/new" in out and "fact/new" in out
    assert "conf=0.9" in out


def test_share_list_hides_quarantined_without_all(tmp_path, capsys):
    home, _ = seed(tmp_path, capsys)
    cand = staging.load_candidates(home)[0]
    staging.quarantine(home, cand.id)
    code, out, _ = run(capsys, "--home", str(home), "share", "list")
    assert cand.id not in out
    code, out, _ = run(capsys, "--home", str(home), "share", "list", "--all")
    assert cand.id in out and "[QUARANTINED]" in out


def test_share_list_empty(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path / "h"), "share", "list")
    assert code == 0
    assert "nothing staged" in out


def test_share_diff_new_prints_body(tmp_path, capsys):
    home, _ = seed(tmp_path, capsys)
    skill = next(c for c in staging.load_candidates(home) if c.type == "skill")
    code, out, _ = run(capsys, "--home", str(home), "share", "diff", skill.id)
    assert code == 0
    assert "## Failure pattern" in out


def test_share_diff_update_shows_unified_diff(tmp_path, capsys):
    # mode="commit": the diff reads the *working tree* of the clone, and a pr-mode harvest
    # lands the unit on a branch and checks main back out, leaving nothing to diff against.
    home, target = seed(tmp_path, capsys, mode="commit")
    skill = next(c for c in staging.load_candidates(home) if c.type == "skill")
    from mneme_core import harvest

    harvest.apply_batch(home, "acme-knowledge", [skill], push=False)
    props = {
        "proposals": [
            {
                "type": "skill", "edit": "update", "target": "acme-knowledge",
                "target_unit": "skills/deploy-widget",
                "name": "deploy-widget", "description": "Use when deploying the widget service",
                "procedure": "1. preflight\n2. cutover\n3. verify",
                "failure_pattern": "restart loop hits the LB cache",
                "confidence": 0.9, "rationale": "improved",
            }
        ]
    }
    p = tmp_path / "props2.json"
    p.write_text(json.dumps(props), encoding="utf-8")
    run(capsys, "--home", str(home), "distill", "ingest", str(p))
    update = next(c for c in staging.load_candidates(home) if c.edit == "update")
    code, out, _ = run(capsys, "--home", str(home), "share", "diff", update.id)
    assert code == 0
    assert "+3.verify" in out.replace(" ", "")


def test_share_diff_unknown_id(tmp_path, capsys):
    code, _, err = run(capsys, "--home", str(tmp_path / "h"), "share", "diff", "nope")
    assert code == 1
    assert "mneme:" in err
