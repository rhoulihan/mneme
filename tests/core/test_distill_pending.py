from mneme_core import flags, paths
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


def test_pending_survives_a_corrupt_flag_line(tmp_path, capsys):
    # `distill pending` is the hook's gate: a raw JSONDecodeError here printed a
    # traceback and exited 1, which the hook reads as "nothing pending" — one
    # truncated line silently disabled distillation forever.
    home = tmp_path / "home"
    flags.add_flag(home, "x")
    with paths.flags_path(home).open("a", encoding="utf-8") as f:
        f.write("this line is corrupt {\n")
    code, out, err = run(capsys, "--home", str(home), "distill", "pending")
    assert code == 0
    assert out.strip() == "1"
    assert "Traceback" not in err
