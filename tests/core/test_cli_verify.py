from datetime import datetime, timedelta, timezone

from mneme_core import registry, scaffold, units
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def old_date(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def make_kb(tmp_path, home):
    target = scaffold.create(home, "acme-knowledge", owner="demo")
    d = target / "skills" / "old-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: old-skill\ndescription: d\nmetadata:\n"
        f"  mneme-last-verified: {old_date(200)}\n---\nBody\n",
        encoding="utf-8",
    )
    (units.facts_dir(target) / "mixed.md").write_text(
        "---\ntopic: mixed\n---\n"
        f"- [gotcha] Fresh fact #x (verified: {old_date(5)})\n"
        f"- [gotcha] Stale fact number two #x (verified: {old_date(120)})\n"
        "- [gotcha] Dateless fact number three #x\n",
        encoding="utf-8",
    )
    import subprocess

    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(target),
         "add", "-A"], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", "-C", str(target),
         "commit", "-m", "fixtures"], check=True, capture_output=True,
    )
    return target


def test_verify_reports_stale_units(tmp_path, capsys):
    home = tmp_path / "home"
    make_kb(tmp_path, home)
    code, out, _ = run(capsys, "--home", str(home), "verify", "acme-knowledge")
    assert code == 2
    assert "skills/old-skill" in out
    assert "last-verified=none" in out          # the dateless fact
    assert "stale 3 of" in out                   # old-skill + stale fact + dateless fact
    assert "Fresh fact" not in out


def test_verify_days_override(tmp_path, capsys):
    home = tmp_path / "home"
    make_kb(tmp_path, home)
    code, out, _ = run(capsys, "--home", str(home), "verify", "acme-knowledge", "--days", "365")
    assert code == 2                              # dateless fact is always stale
    assert "stale 1 of" in out


def test_verify_all_fresh_exits_0(tmp_path, capsys):
    home = tmp_path / "home"
    target = scaffold.create(home, "fresh-kb", owner="demo")
    (units.facts_dir(target) / "t.md").write_text(
        f"---\ntopic: t\n---\n- [gotcha] Fresh #x (verified: {old_date(1)})\n",
        encoding="utf-8",
    )
    code, out, _ = run(capsys, "--home", str(home), "verify", "fresh-kb")
    assert code == 0
    assert "stale 0 of" in out


def test_verify_sweeps_a_legacy_facts_layout(tmp_path, capsys):
    """A repo that still keeps facts at the top level is swept the same way."""
    home = tmp_path / "home"
    target = scaffold.create(home, "legacy-verify-kb", owner="demo")
    (target / units.FACTS_CANONICAL / ".gitkeep").unlink()
    (target / units.FACTS_CANONICAL).rmdir()
    legacy = target / "facts"
    legacy.mkdir()
    (legacy / "mixed.md").write_text(
        "---\ntopic: mixed\n---\n"
        f"- [gotcha] Fresh fact #x (verified: {old_date(5)})\n"
        f"- [gotcha] Stale fact number two #x (verified: {old_date(120)})\n",
        encoding="utf-8",
    )
    code, out, _ = run(capsys, "--home", str(home), "verify", "legacy-verify-kb")
    assert code == 2
    assert "facts/mixed#stale-fact-number-two" in out
    assert "stale 1 of 2" in out


def test_verify_unknown_plugin(tmp_path, capsys):
    code, _, err = run(capsys, "--home", str(tmp_path / "h"), "verify", "ghost")
    assert code == 1
    assert "mneme:" in err
