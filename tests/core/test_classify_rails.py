import pytest

from mneme_core import classify, gitops, scaffold
from mneme_core.cli import main
from mneme_core.errors import MnemeError


def make_kb(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "classify-kb", owner="demo")
    return home, target


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_begin_creates_branch_from_clean_main(tmp_path):
    home, target = make_kb(tmp_path)
    branch = classify.begin(home, target)
    assert branch.startswith("mneme/classify-")
    assert gitops.current_branch(target) == branch


def test_begin_resolves_from_subdirectory(tmp_path):
    home, target = make_kb(tmp_path)
    branch = classify.begin(home, target / "skills")
    assert gitops.current_branch(target) == branch


def test_begin_refuses_dirty_or_double(tmp_path):
    home, target = make_kb(tmp_path)
    (target / "junk.txt").write_text("x", encoding="utf-8")
    with pytest.raises(MnemeError):
        classify.begin(home, target)
    (target / "junk.txt").unlink()
    classify.begin(home, target)
    with pytest.raises(MnemeError):
        classify.begin(home, target)


def test_abort_restores_and_deletes(tmp_path):
    home, target = make_kb(tmp_path)
    branch = classify.begin(home, target)
    (target / "MNEME.md").write_text("mutated", encoding="utf-8")
    (target / "stray.txt").write_text("x", encoding="utf-8")
    classify.abort(home, target)
    assert gitops.current_branch(target) == "main"
    assert gitops.is_clean(target)
    assert branch not in gitops.git(target, "branch", "--list", "mneme/classify-*")


def test_abort_outside_classify_branch_refuses(tmp_path):
    home, target = make_kb(tmp_path)
    with pytest.raises(MnemeError):
        classify.abort(home, target)


def test_unregistered_directory_fails_clearly(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(MnemeError) as exc:
        classify.begin(tmp_path / "home", plain)
    assert "not inside a registered knowledge plugin" in str(exc.value)


def test_cli_begin_then_abort(tmp_path, capsys):
    home, target = make_kb(tmp_path)
    code, out, _ = run(
        capsys, "--home", str(home), "classify", "begin", "--cwd", str(target / "skills")
    )
    assert code == 0
    branch = out.strip()
    assert branch.startswith("mneme/classify-")
    assert gitops.current_branch(target) == branch
    code, out, _ = run(capsys, "--home", str(home), "classify", "abort", "--cwd", str(target))
    assert code == 0
    assert out.strip() == "aborted"
    assert gitops.current_branch(target) == "main"
    assert branch not in gitops.git(target, "branch", "--list", "mneme/classify-*")


def test_cli_cwd_defaults_to_process_directory(tmp_path, capsys, monkeypatch):
    home, target = make_kb(tmp_path)
    monkeypatch.chdir(target / "skills")
    code, out, _ = run(capsys, "--home", str(home), "classify", "begin")
    assert code == 0
    assert gitops.current_branch(target) == out.strip()


def test_cli_outside_plugin_exits_one_with_message(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    code, _, err = run(
        capsys, "--home", str(tmp_path / "h"), "classify", "begin", "--cwd", str(plain)
    )
    assert code == 1
    assert "not inside a registered knowledge plugin" in err
