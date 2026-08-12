import json

import pytest

from mneme_core import flags, paths
from mneme_core.errors import MnemeError


def test_add_and_read_flags(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    rec = flags.add_flag(tmp_path, "solved the widget deploy race", session="s-1")
    assert rec["kind"] == "golden-path"
    assert rec["session"] == "s-1"
    assert rec["ts"].endswith("+00:00")

    flags.add_flag(tmp_path, "docs said X but reality is Y", kind="knowledge-issue")
    all_flags = flags.read_flags(tmp_path)
    assert len(all_flags) == 2
    assert all_flags[1]["kind"] == "knowledge-issue"
    assert all_flags[1]["session"] == "unknown"


def test_session_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "env-session")
    rec = flags.add_flag(tmp_path, "note")
    assert rec["session"] == "env-session"


def test_empty_text_rejected(tmp_path):
    with pytest.raises(MnemeError):
        flags.add_flag(tmp_path, "   ")


def test_unknown_kind_rejected(tmp_path):
    with pytest.raises(MnemeError):
        flags.add_flag(tmp_path, "x", kind="misc")


def test_read_missing_and_clear(tmp_path):
    assert flags.read_flags(tmp_path) == []
    flags.add_flag(tmp_path, "x")
    flags.clear_flags(tmp_path)
    assert flags.read_flags(tmp_path) == []
    flags.clear_flags(tmp_path)  # idempotent


def test_corrupt_line_does_not_break_reading(tmp_path, capsys):
    # A killed pipeline or an interrupted `mneme flag` leaves a half-written last
    # line. Before, json.JSONDecodeError escaped read_flags and killed the hook's
    # `distill pending` gate — distillation stayed dead while flags piled up.
    flags.add_flag(tmp_path, "good one")
    with paths.flags_path(tmp_path).open("a", encoding="utf-8") as f:
        f.write("this line is corrupt {\n")
    flags.add_flag(tmp_path, "after the corruption")

    records = flags.read_flags(tmp_path)
    assert [r["text"] for r in records] == ["good one", "after the corruption"]
    assert "skipped 1 unreadable line" in capsys.readouterr().err


def test_consume_flags_keeps_records_added_after_the_snapshot(tmp_path):
    flags.add_flag(tmp_path, "before the distiller ran")
    snapshot = flags.read_flags(tmp_path)
    mid_run = flags.add_flag(tmp_path, "flagged while the distiller was thinking")

    assert flags.consume_flags(tmp_path, snapshot) == 1
    assert flags.read_flags(tmp_path) == [mid_run]


def test_consume_flags_preserves_corrupt_lines(tmp_path):
    flags.add_flag(tmp_path, "distilled")
    snapshot = flags.read_flags(tmp_path)
    with paths.flags_path(tmp_path).open("a", encoding="utf-8") as f:
        f.write("truncated {\n")

    flags.consume_flags(tmp_path, snapshot)
    assert "truncated {" in paths.flags_path(tmp_path).read_text(encoding="utf-8")
    assert flags.read_flags(tmp_path) == []


def test_consume_flags_removes_the_file_when_nothing_is_left(tmp_path):
    flags.add_flag(tmp_path, "only one")
    flags.consume_flags(tmp_path, flags.read_flags(tmp_path))
    assert not paths.flags_path(tmp_path).exists()
    assert flags.consume_flags(tmp_path, []) == 0  # missing file is not an error


def test_consume_flags_removes_one_line_per_duplicate_record(tmp_path):
    rec = flags.add_flag(tmp_path, "same text same second", session="s")
    paths.flags_path(tmp_path).write_text(
        json.dumps(rec) + "\n" + json.dumps(rec) + "\n", encoding="utf-8"
    )
    assert flags.consume_flags(tmp_path, [rec]) == 1
    assert flags.read_flags(tmp_path) == [rec]
