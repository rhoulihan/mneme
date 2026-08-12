import json
from pathlib import Path

from mneme_core import staging
from mneme_core.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED = REPO_ROOT / "docs" / "dogfood" / "seed-proposals.json"


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_seed_document_is_valid_and_substantive():
    data = json.loads(SEED.read_text(encoding="utf-8"))
    entries = data["proposals"]
    assert len(entries) == 9
    kinds = [e["type"] for e in entries]
    assert kinds.count("skill") == 3
    assert kinds.count("fact") == 6
    for e in entries:
        assert e["rationale"], e
        if e["type"] == "skill":
            assert "failure" in json.dumps(e).lower() or e["failure_pattern"], e


def test_seed_clears_the_real_gate(tmp_path, capsys):
    home = tmp_path / "home"
    code, out, _ = run(
        capsys, "--home", str(home), "distill", "ingest", str(SEED),
        "--source", "mneme-build@plans-01-07",
    )
    assert code == 0
    assert "staged 9" in out
    assert "quarantined 0" in out
    assert "rejected 0" in out
    cands = staging.load_candidates(home)
    assert len(cands) == 9
    assert all(c.target == "mneme-dev-knowledge" for c in cands)
