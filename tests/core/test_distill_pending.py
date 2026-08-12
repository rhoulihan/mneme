from mneme_core import flags
from mneme_core.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_pending_none(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path / "h"), "distill", "pending")
    assert code == 1
    assert out.strip() == "0"


def test_pending_some(tmp_path, capsys):
    home = tmp_path / "home"
    flags.add_flag(home, "x")
    flags.add_flag(home, "y")
    code, out, _ = run(capsys, "--home", str(home), "distill", "pending")
    assert code == 0
    assert out.strip() == "2"
