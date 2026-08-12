from mneme_core import paths
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_kb(tmp_path, name="declined-kb"):
    kb = tmp_path / name
    kb.mkdir()
    (kb / "MNEME.md").write_text("# scope\n", encoding="utf-8")
    return kb


def test_decline_suppresses_nudge_persistently(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert "Unregistered knowledge repo detected" in out
    code, out, _ = run(capsys, "--home", str(home), "detection", "decline", "--cwd", str(kb))
    assert code == 0
    assert "declined" in out
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert code == 0
    assert "Unregistered knowledge repo detected" not in out
    assert "mneme noticing" in out  # brief itself unaffected


def test_decline_is_idempotent_and_listed(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    run(capsys, "--home", str(home), "detection", "decline", "--cwd", str(kb))
    run(capsys, "--home", str(home), "detection", "decline", "--cwd", str(kb))
    code, out, _ = run(capsys, "--home", str(home), "detection", "list")
    assert code == 0
    assert out.strip().splitlines().count(str(kb.resolve())) == 1


def test_decline_outside_kb_fails(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    code, _, err = run(
        capsys, "--home", str(tmp_path / "h"), "detection", "decline", "--cwd", str(plain)
    )
    assert code == 1
    assert "mneme:" in err


def test_corrupt_ledger_lines_skipped(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    paths.ensure_layout(home)
    with paths.detection_declined_path(home).open("a", encoding="utf-8") as f:
        f.write("{corrupt\n")
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert code == 0
    assert "Unregistered knowledge repo detected" in out  # corrupt line neither crashes nor suppresses


def test_declined_subdirectory_resolves_to_the_repo_root(tmp_path, capsys):
    """Declining from inside the repo declines the repo, not the subdirectory."""
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    deep = kb / "skills" / "thing"
    deep.mkdir(parents=True)
    code, out, _ = run(capsys, "--home", str(home), "detection", "decline", "--cwd", str(deep))
    assert code == 0
    assert out.strip() == f"declined {kb.resolve()}"
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(deep))
    assert "Unregistered knowledge repo detected" not in out


def test_nudge_tells_the_agent_how_to_persist_a_decline(tmp_path, capsys):
    home = tmp_path / "home"
    kb = make_kb(tmp_path)
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb))
    assert code == 0
    assert f"mneme detection decline --cwd {kb.resolve()}" in out
    assert "not be asked again" in out


def test_declining_one_repo_does_not_suppress_another(tmp_path, capsys):
    home = tmp_path / "home"
    kb_a = make_kb(tmp_path, name="kb-a")
    kb_b = make_kb(tmp_path, name="kb-b")
    run(capsys, "--home", str(home), "detection", "decline", "--cwd", str(kb_a))
    code, out, _ = run(capsys, "--home", str(home), "context", "--cwd", str(kb_b))
    assert code == 0
    assert "Unregistered knowledge repo detected" in out
    assert str(kb_b.resolve()) in out


def test_detection_list_without_a_ledger_is_empty_and_clean(tmp_path, capsys):
    code, out, err = run(capsys, "--home", str(tmp_path / "nohome"), "detection", "list")
    assert code == 0
    assert out.strip() == ""
    assert err == ""
