import json

from mneme_core import registry, staging
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def fact_entry(**kw):
    entry = dict(
        type="fact", edit="new", target="acme-knowledge",
        topic="staging-env", category="constraint",
        text="Staging DB resets nightly at 04:00 UTC", tags=["staging"],
        confidence=0.7, rationale="observed twice",
    )
    entry.update(kw)
    return entry


def write_proposals(tmp_path, entries):
    p = tmp_path / "proposals.json"
    p.write_text(json.dumps({"proposals": entries}), encoding="utf-8")
    return str(p)


def make_kb(tmp_path, name="acme-knowledge", sensitivity="internal"):
    kb = tmp_path / name
    facts = kb / "facts"
    facts.mkdir(parents=True)
    (facts / "staging-env.md").write_text(
        "---\ntopic: staging-env\n---\n"
        "- [constraint] Staging DB resets nightly at 04:00 UTC #staging (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    (kb / "MNEME.md").write_text(
        f"# {name}\n\n## Scope statement\n\nWidget ops.\n", encoding="utf-8"
    )
    return kb


def test_similar_to_annotated_when_index_exists(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    registry.add_plugin(home, Plugin(name="acme-knowledge", repo="r", path=str(kb)))
    run(capsys, "--home", str(home), "index", "rebuild")
    path = write_proposals(tmp_path, [fact_entry(text="The staging DB resets nightly around 04:00")])
    code, _, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    cand = staging.load_candidates(home)[0]
    assert cand.similar_to == "facts/staging-env#staging-db-resets-nightly-at-04"


def test_no_index_no_annotation_no_failure(tmp_path, capsys):
    home = tmp_path / "home"
    path = write_proposals(tmp_path, [fact_entry()])
    code, _, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert staging.load_candidates(home)[0].similar_to == ""


def test_boundary_warning_on_less_restricted_target(tmp_path, capsys):
    home = tmp_path / "home"
    restricted = make_kb(tmp_path, name="secret-kb", sensitivity="restricted")
    public = make_kb(tmp_path, name="public-kb")
    registry.add_plugin(
        home, Plugin(name="secret-kb", repo="r", path=str(restricted), sensitivity="restricted")
    )
    registry.add_plugin(
        home, Plugin(name="public-kb", repo="r", path=str(public), sensitivity="public")
    )
    path = write_proposals(tmp_path, [fact_entry(target="public-kb")])
    code, out, _ = run(
        capsys, "--home", str(home), "distill", "ingest", path,
        "--source-plugin", "secret-kb",
    )
    assert code == 0
    assert "boundary-warnings 1" in out
    cand = staging.load_candidates(home)[0]
    assert "public-kb" in cand.boundary_warning
    assert "restricted" in cand.boundary_warning


def test_no_warning_without_source_plugin_or_for_unassigned(tmp_path, capsys):
    home = tmp_path / "home"
    public = make_kb(tmp_path, name="public-kb")
    registry.add_plugin(
        home, Plugin(name="public-kb", repo="r", path=str(public), sensitivity="public")
    )
    path = write_proposals(
        tmp_path, [fact_entry(target="public-kb"), fact_entry(target=None, topic="misc-topic")]
    )
    code, out, _ = run(capsys, "--home", str(home), "distill", "ingest", path)
    assert code == 0
    assert "boundary-warnings 0" in out
    for cand in staging.load_candidates(home):
        assert cand.boundary_warning == ""
