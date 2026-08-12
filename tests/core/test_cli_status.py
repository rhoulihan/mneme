import json

from mneme_core import flags, paths, registry, staging
from mneme_core.cli import main
from mneme_core.registry import Plugin
from mneme_core.staging import Candidate, candidate_id


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_status_fresh_home(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path / "h"), "status")
    assert code == 0
    assert "plugins: 0 registered" in out
    assert "flags: 0 pending" in out
    assert "staging: 0 staged, 0 quarantined, 0 declined" in out
    assert "submissions: 0 recorded" in out
    assert "index: not built" in out


def test_status_populated(tmp_path, capsys):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="acme-knowledge", repo="r", path="/x"))
    flags.add_flag(home, "learned a thing")
    body = "- [gotcha] Something #x (verified: 2026-08-11)\n"
    cand = Candidate(
        id=candidate_id("fact", "acme-knowledge", body), type="fact", edit="new",
        target="acme-knowledge", body=body, topic="t",
    )
    staging.write_candidate(home, cand)
    staging.decline(home, cand, "nope")
    paths.ensure_layout(home)
    record = {"target": "acme-knowledge", "branch": "mneme/harvest-x", "units": ["u"]}
    with paths.submitted_path(home).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    code, out, _ = run(capsys, "--home", str(home), "status")
    assert code == 0
    assert "plugins: 1 registered" in out
    assert "- acme-knowledge [internal]" in out
    assert "flags: 1 pending" in out
    assert "0 staged" in out and "1 declined" in out
    assert "submissions: 1 recorded" in out
    assert "mneme/harvest-x" in out


def test_status_index_enabled(tmp_path, capsys):
    home = tmp_path / "home"
    kb = tmp_path / "kb"
    d = kb / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: a-skill\ndescription: d\n---\nBody\n", encoding="utf-8"
    )
    registry.add_plugin(home, Plugin(name="kb", repo="r", path=str(kb)))
    run(capsys, "--home", str(home), "index", "rebuild")
    code, out, _ = run(capsys, "--home", str(home), "status")
    assert code == 0
    assert "index: enabled" in out
