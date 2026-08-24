"""Where a flag was captured — the input the boundary check has never had.

`cli._distill_ingest` computes the `[boundary]` warning from the SOURCE context's
sensitivity, resolved from `--source-plugin`. `bin/mneme-distill-pipeline` has never passed
that flag; it passes only `--source "session:<transcript>"`. So `source_scope` is always
None in the shipped path, every candidate is staged with an empty warning, and ingest
reports `boundary-warnings 0` no matter what it staged. The one guard against restricted
knowledge drifting toward a less-restricted repo has therefore never fired outside a
hand-run ingest.

The fix is not in the shell script. The pipeline already hands ingest the bundle as
`--flags-snapshot`, and the bundle already carries the flag records — so a flag that
remembers where it was captured lets ingest work it out with no change to the pipeline at
all, and it works for anyone driving `distill ingest` by hand too.
"""
import json
import subprocess

import pytest

from mneme_core import flags, gitops, registry, scaffold, staging
from mneme_core.cli import main
from mneme_core.registry import Plugin

PROPOSAL = {
    "type": "fact",
    "edit": "new",
    "target": "{target}",
    "topic": "webhooks",
    "category": "gotcha",
    "text": "The webhook replays for seventy two hours",
    "tags": ["webhooks"],
    "confidence": 0.9,
    "rationale": "measured",
}


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def kb(tmp_path, home, name, sensitivity):
    return scaffold.create(
        home, name, owner="demo", directory=tmp_path / name, sensitivity=sensitivity
    )


def ingest(tmp_path, home, capsys, target, snapshot_flags, extra=()):
    """Run `distill ingest` the way the pipeline runs it — snapshot, no --source-plugin."""
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"flags": snapshot_flags}), encoding="utf-8")
    payload = tmp_path / "proposals.json"
    p = dict(PROPOSAL, target=target)
    payload.write_text(json.dumps({"proposals": [p]}), encoding="utf-8")
    return run(
        capsys, "--home", str(home), "distill", "ingest", str(payload),
        "--source", "session:abc", "--flags-snapshot", str(bundle), *extra,
    )


# --- the flag remembers where it was --------------------------------------------


def test_a_flag_records_where_it_was_captured(tmp_path):
    home = tmp_path / "home"
    here = tmp_path / "somewhere"
    here.mkdir()
    from mneme_core import paths

    rec = flags.add_flag(home, "a thing I learned", cwd=here)
    assert rec["cwd"] == str(here.resolve())
    line = paths.flags_path(home).read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(line)["cwd"] == str(here.resolve())


def test_a_flag_written_before_this_still_loads(tmp_path):
    """Absent on every flag captured before the field existed, and read as unknown."""
    home = tmp_path / "home"
    from mneme_core import paths

    flags.add_flag(home, "old flag")

    p = paths.flags_path(home)
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    del rec["cwd"]
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    records, bad = flags._read_flag_lines(home)
    assert bad == 0 and records[0].get("cwd", "") == ""


# --- ingest works the source out from the snapshot ------------------------------


def test_the_boundary_fires_through_the_shipped_path(tmp_path, capsys):
    """No --source-plugin anywhere — exactly how `mneme-distill-pipeline` calls it."""
    home = tmp_path / "home"
    secret = kb(tmp_path, home, "secret-kb", "restricted")
    kb(tmp_path, home, "open-kb", "public")
    snapshot = [flags.add_flag(home, "learned inside the restricted repo", cwd=secret)]

    code, out, _ = ingest(tmp_path, home, capsys, "open-kb", snapshot)

    assert code == 0
    assert "boundary-warnings 1" in out, out
    cand = staging.load_candidates(home)[0]
    assert cand.source_sensitivity == "restricted"
    assert "restricted" in cand.boundary_warning


def test_no_boundary_when_the_move_is_toward_more_restriction(tmp_path, capsys):
    home = tmp_path / "home"
    kb(tmp_path, home, "secret-kb", "restricted")
    open_kb = kb(tmp_path, home, "open-kb", "public")
    snapshot = [flags.add_flag(home, "learned in the open repo", cwd=open_kb)]

    _code, out, _ = ingest(tmp_path, home, capsys, "secret-kb", snapshot)

    assert "boundary-warnings 0" in out
    assert staging.load_candidates(home)[0].boundary_warning == ""


def test_the_most_restricted_flag_decides(tmp_path, capsys):
    """A session that touched two repos is judged by the tighter one. Taking the first
    would let mixing a restricted repo into the session launder everything in it."""
    home = tmp_path / "home"
    open_kb = kb(tmp_path, home, "open-kb", "public")
    mid = kb(tmp_path, home, "mid-kb", "internal")
    secret = kb(tmp_path, home, "secret-kb", "restricted")
    snapshot = [
        flags.add_flag(home, "first, in the open repo", cwd=open_kb),
        flags.add_flag(home, "then the restricted one", cwd=secret),
        flags.add_flag(home, "then an internal one", cwd=mid),
    ]

    _code, out, _ = ingest(tmp_path, home, capsys, "open-kb", snapshot)

    assert "boundary-warnings 1" in out
    assert staging.load_candidates(home)[0].source_sensitivity == "restricted"


def test_a_flag_from_outside_every_registered_repo_says_nothing(tmp_path, capsys):
    """Unknown is the honest answer, and the one `staging.route` already reports as
    "unverified" rather than implying a check that did not happen."""
    home = tmp_path / "home"
    kb(tmp_path, home, "open-kb", "public")
    elsewhere = tmp_path / "some-app"
    elsewhere.mkdir()
    snapshot = [flags.add_flag(home, "learned in an unregistered repo", cwd=elsewhere)]

    _code, out, _ = ingest(tmp_path, home, capsys, "open-kb", snapshot)

    assert "boundary-warnings 0" in out
    assert staging.load_candidates(home)[0].source_sensitivity == ""


def test_an_explicit_source_plugin_still_wins(tmp_path, capsys):
    """The flag is a fallback, not an override — a caller that knows must be believed."""
    home = tmp_path / "home"
    kb(tmp_path, home, "secret-kb", "restricted")
    open_kb = kb(tmp_path, home, "open-kb", "public")
    snapshot = [flags.add_flag(home, "captured in the open repo", cwd=open_kb)]

    _code, out, _ = ingest(
        tmp_path, home, capsys, "open-kb", snapshot,
        extra=("--source-plugin", "secret-kb"),
    )

    assert "boundary-warnings 1" in out
    assert staging.load_candidates(home)[0].source_sensitivity == "restricted"


def test_flags_without_an_origin_do_not_break_ingest(tmp_path, capsys):
    home = tmp_path / "home"
    kb(tmp_path, home, "open-kb", "public")
    snapshot = [{"ts": "2026-08-01", "session": "x", "kind": "golden-path", "text": "old"}]

    code, out, _ = ingest(tmp_path, home, capsys, "open-kb", snapshot)

    assert code == 0
    assert "boundary-warnings 0" in out


def test_the_cli_flag_command_records_the_cwd(tmp_path, capsys):
    home = tmp_path / "home"
    repo = kb(tmp_path, home, "secret-kb", "restricted")
    code, _out, _ = run(
        capsys, "--home", str(home), "flag", "something learned", "--cwd", str(repo)
    )
    assert code == 0
    from mneme_core import paths

    rec = json.loads(paths.flags_path(home).read_text(encoding="utf-8").splitlines()[0])
    assert rec["cwd"] == str(repo.resolve())
