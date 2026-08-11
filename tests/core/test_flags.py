import pytest

from mneme_core import flags
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
