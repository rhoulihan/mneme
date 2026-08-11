from mneme_index.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_unknown_subcommand_exits_1(tmp_path, capsys):
    code, _, err = run(capsys, "--db", str(tmp_path / "i.db"), "frobnicate")
    assert code == 1
    assert "mneme-index:" in err


def test_missing_required_db_exits_1(capsys):
    code, _, err = run(capsys, "status")
    assert code == 1
    assert "mneme-index:" in err


def test_missing_subcommand_exits_1(tmp_path, capsys):
    code, _, err = run(capsys, "--db", str(tmp_path / "i.db"))
    assert code == 1
