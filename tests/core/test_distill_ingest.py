import json

from mneme_core import compose, flags, staging
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def skill_entry(**kw):
    entry = dict(
        type="skill", edit="new", target="acme-knowledge",
        name="deploy-widget", description="Use when deploying widgets",
        procedure="Steps.", failure_pattern="What failed first.",
        confidence=0.8, rationale="verified in session",
    )
    entry.update(kw)
    return entry


def fact_entry(**kw):
    entry = dict(
        type="fact", edit="new", target="acme-knowledge",
        topic="staging-env", category="constraint",
        text="DB resets nightly", tags=["staging"],
        confidence=0.7, rationale="observed twice",
    )
    entry.update(kw)
    return entry


def write_proposals(tmp_path, entries):
    p = tmp_path / "proposals.json"
    p.write_text(json.dumps({"proposals": entries}), encoding="utf-8")
    return str(p)


def test_ingest_stages_valid_proposals(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(tmp_path, [skill_entry(), fact_entry()])
    code, out, _ = run(
        capsys, "--home", str(home), "distill", "ingest", path, "--source", "repo@s1"
    )
    assert code == 0
    assert "staged 2" in out
    cands = staging.load_candidates(home)
    assert len(cands) == 2
    skill = next(c for c in cands if c.type == "skill")
    assert "## Failure pattern" in skill.body
    assert skill.provenance["source"] == "repo@s1"
    fact = next(c for c in cands if c.type == "fact")
    assert fact.topic == "staging-env"
    assert fact.body.startswith("- [constraint] DB resets nightly #staging")


def test_ingest_quarantines_secrets(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(
        tmp_path, [fact_entry(text="The staging key is AKIAIOSFODNN7EXAMPLE")]
    )
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "quarantined 1" in out
    assert staging.load_candidates(home) == []
    q = staging.load_candidates(home, include_quarantined=True)
    assert len(q) == 1
    assert q[0].status == "quarantined"


def test_ingest_respects_declined_ledger(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(tmp_path, [fact_entry()])
    run(capsys, "--home", str(home), "distill", "ingest", path)
    cand = staging.load_candidates(home)[0]
    staging.decline(home, cand, "not useful")
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "skipped-declined 1" in out
    assert staging.load_candidates(home) == []


def test_ingest_skips_duplicates(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(tmp_path, [fact_entry()])
    run(capsys, "--home", str(home), "distill", "ingest", path)
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "skipped-duplicate 1" in out
    assert len(staging.load_candidates(home)) == 1


def earlier_candidate(body, type_="fact", target="acme-knowledge"):
    """The candidate an ingest run on an earlier day would have produced."""
    return staging.Candidate(
        id=staging.candidate_id(type_, target, body),
        type=type_, edit="new", target=target, body=body,
    )


def test_ingest_respects_declined_ledger_across_a_day_boundary(tmp_path, capsys):
    # The distiller runs repeatedly, re-rendering the same fact with today's stamp. A
    # decline from an earlier day must still suppress it (spec §7.3), or every declined
    # candidate resurfaces at midnight UTC.
    home = tmp_path / "home"
    yesterday = compose.render_fact_bullet(
        "constraint", "DB resets nightly", ["staging"], verified="2000-01-01"
    )
    staging.decline(home, earlier_candidate(yesterday), "not useful")
    path = write_proposals(tmp_path, [fact_entry()])
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "skipped-declined 1" in out
    assert staging.load_candidates(home) == []


def test_ingest_skips_a_duplicate_staged_on_an_earlier_day(tmp_path, capsys):
    home = tmp_path / "home"
    yesterday = compose.render_fact_bullet(
        "constraint", "DB resets nightly", ["staging"], verified="2000-01-01"
    )
    staging.write_candidate(home, earlier_candidate(yesterday))
    path = write_proposals(tmp_path, [fact_entry()])
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "skipped-duplicate 1" in out
    assert len(staging.load_candidates(home)) == 1


def test_ingest_skips_a_skill_duplicate_from_an_earlier_day_and_session(tmp_path, capsys):
    home = tmp_path / "home"
    yesterday = compose.render_skill_unit(
        "deploy-widget", "Use when deploying widgets", "Steps.", "What failed first.",
        source="repo@s1", captured="2000-01-01",
    )
    staging.write_candidate(home, earlier_candidate(yesterday, type_="skill"))
    path = write_proposals(tmp_path, [skill_entry()])
    code, out, _ = run(
        capsys, "--home", str(home), "distill", "ingest", path, "--source", "repo@s2"
    )
    assert code == 0
    assert "skipped-duplicate 1" in out
    assert len(staging.load_candidates(home)) == 1


def test_ingest_deeply_nested_json_fails_gracefully(tmp_path, capsys):
    # The proposals file is LLM output — the trust boundary. Deep nesting raises
    # RecursionError out of the C JSON scanner, not JSONDecodeError, and it must still
    # honour the exit-code contract instead of printing a traceback.
    deep = tmp_path / "deep.json"
    deep.write_text('{"proposals": ' + "[" * 60000 + "]" * 60000 + "}", encoding="utf-8")
    code, _, err = run(capsys, "--home", str(tmp_path / "h"), "distill", "ingest", str(deep))
    assert code == 1
    assert err.startswith("mneme:")
    assert "Traceback" not in err


def test_ingest_reports_rejected(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(tmp_path, [skill_entry(name="Bad_Name"), fact_entry()])
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "rejected 1" in out
    assert "rejected: proposal 0:" in out
    assert len(staging.load_candidates(home)) == 1


def test_ingest_clear_flags(tmp_path, capsys):
    home = tmp_path / "home"
    flags.add_flag(home, "something")
    path = write_proposals(tmp_path, [fact_entry()])
    run(capsys, "--home", str(home), "distill", "ingest", path, "--clear-flags")
    assert flags.read_flags(home) == []


def test_ingest_bad_document_exits_1(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    code, _, err = run(capsys, "--home", str(tmp_path / "h"), "distill", "ingest", str(bad))
    assert code == 1
    assert "mneme:" in err
