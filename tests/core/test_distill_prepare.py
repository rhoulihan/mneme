import json

from mneme_core import flags, registry
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_prepare_bundles_scopes_and_flags(tmp_path, capsys):
    home = tmp_path / "home"
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "MNEME.md").write_text(
        "# x\n\n## Scope statement\n\nWidget ops.\nSecond line.\n", encoding="utf-8"
    )
    registry.add_plugin(home, Plugin(name="acme-knowledge", repo="r", path=str(kb)))
    flags.add_flag(home, "solved the deploy race", session="s1")
    code, out, _ = run(
        capsys, "--home", str(home), "distill", "prepare", "--transcript", "/tmp/t.jsonl"
    )
    assert code == 0
    bundle = json.loads(out)
    assert bundle["flag_count"] == 1
    prompt = bundle["prompt"]
    assert "- acme-knowledge [internal/pr]: Widget ops. Second line." in prompt
    assert "solved the deploy race" in prompt
    assert "/tmp/t.jsonl" in prompt
    assert '"proposals"' in prompt


def test_prepare_empty_home(tmp_path, capsys):
    code, out, _ = run(capsys, "--home", str(tmp_path / "home"), "distill", "prepare")
    assert code == 0
    bundle = json.loads(out)
    assert bundle["flag_count"] == 0
    assert "(none registered)" in bundle["prompt"]
    assert "(no flags this session)" in bundle["prompt"]
    assert "(not provided)" in bundle["prompt"]
