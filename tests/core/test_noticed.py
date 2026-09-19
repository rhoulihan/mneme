"""Holding a candidate between the event that saw it and the event that can speak.

Verified against Claude Code 2.1.278: `Stop` cannot inject context and its stdout is
discarded; `UserPromptSubmit` can, and is the only event that both fires repeatedly during
a session and can put text in front of the model. So detection and delivery are different
moments and something has to carry a candidate across.
"""
import json

import pytest

from mneme_core import detect, noticed, tally, transcript
from mneme_core.cli import main


def sig(kind="resolved-error", evidence="ORA-01000: maximum open cursors exceeded",
        detail="ORA-01000 then a success of `sqlplus`, after 4 failures"):
    return detect.Signal(kind=kind, evidence=evidence, detail=detail)


def run(capsys, *argv):
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_a_signal_is_held_until_it_is_delivered(tmp_path):
    home = tmp_path / "home"
    assert noticed.record_signals(home, "s1", [sig()]) == 1
    pending = noticed.unreported(home, "s1")
    assert len(pending) == 1
    assert "ORA-01000" in pending[0].detail


def test_the_same_signal_is_not_held_twice(tmp_path):
    """Stop fires once per TURN and re-detects over the whole transcript each time, so
    every candidate is re-offered on every turn unless it is deduplicated."""
    home = tmp_path / "home"
    noticed.record_signals(home, "s1", [sig()])
    assert noticed.record_signals(home, "s1", [sig()]) == 0
    assert len(noticed.read_noticed(home)) == 1


def test_a_delivered_candidate_is_not_delivered_again(tmp_path):
    home = tmp_path / "home"
    noticed.record_signals(home, "s1", [sig()])
    pending = noticed.unreported(home, "s1")
    noticed.mark_reported(home, pending)
    assert noticed.unreported(home, "s1") == []


def test_candidates_are_scoped_to_their_session(tmp_path):
    home = tmp_path / "home"
    noticed.record_signals(home, "s1", [sig()])
    assert noticed.unreported(home, "s2") == []


def test_only_a_few_arrive_at_once(tmp_path):
    """N2, the interruption budget. A wall of candidates is ignored wholesale, which is
    worse than no prompt at all."""
    home = tmp_path / "home"
    noticed.record_signals(
        home, "s1", [sig(detail=f"ORA-0000{i} then a success") for i in range(9)]
    )
    assert len(noticed.unreported(home, "s1")) == noticed.MAX_PER_PROMPT


def test_the_ledger_is_bounded(tmp_path):
    home = tmp_path / "home"
    noticed.record_signals(
        home, "s1", [sig(detail=f"d{i}") for i in range(noticed.MAX_RECORDS + 40)]
    )
    assert len(noticed.read_noticed(home)) == noticed.MAX_RECORDS


def test_an_enormous_signal_is_capped(tmp_path):
    home = tmp_path / "home"
    noticed.record_signals(home, "s1", [sig(evidence="x" * 100_000, detail="y" * 100_000)])
    held = noticed.read_noticed(home)[0]
    assert len(held.evidence) == noticed.MAX_FIELD
    assert len(held.detail) == noticed.MAX_FIELD


def test_the_rendering_names_the_finding(tmp_path):
    """R2's standard: a generic "flag anything useful?" is dismissed; naming the error code
    and what resolved it is not."""
    home = tmp_path / "home"
    noticed.record_signals(home, "s1", [sig()])
    text = noticed.render(noticed.unreported(home, "s1"))
    assert "ORA-01000" in text
    assert "mneme flag" in text


def test_nothing_noticed_renders_nothing(tmp_path):
    """N4: silent when idle."""
    assert noticed.render([]) == ""


# --- through the CLI ---------------------------------------------------------


def transcript_file(tmp_path, *lines):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def tool_use(name, tid, **inp):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "id": tid, "input": inp}]}})


def tool_result(tid, content="", is_error=False):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": content,
         "is_error": is_error}]}})


def a_session_with_a_finding(tmp_path):
    lines = []
    for i in range(2):
        lines += [tool_use("Bash", f"f{i}", command=f"sqlplus @a{i}.sql"),
                  tool_result(f"f{i}", "ORA-01000: maximum open cursors exceeded",
                              is_error=True)]
    lines += [tool_use("Bash", "ok", command="sqlplus @fixed.sql"),
              tool_result("ok", "done")]
    return transcript_file(tmp_path, *lines)


def test_the_stop_path_detects_and_the_prompt_path_delivers(tmp_path, capsys):
    """The whole loop: Stop sees, UserPromptSubmit speaks."""
    home = tmp_path / "home"
    t = a_session_with_a_finding(tmp_path)

    run(capsys, "--home", str(home), "session", "tally", "--transcript", str(t),
        "--session", "s1")
    assert noticed.count_for(home, "s1") == 1

    _code, out, _err = run(capsys, "--home", str(home), "session", "prompt",
                           "--session", "s1")
    assert "ORA-01000" in out

    _code, again, _err = run(capsys, "--home", str(home), "session", "prompt",
                             "--session", "s1")
    assert again.strip() == "", "the same candidate was delivered twice"


def test_the_prompt_is_silent_for_a_session_with_no_findings(tmp_path, capsys):
    home = tmp_path / "home"
    t = transcript_file(tmp_path, tool_use("Bash", "a", command="ls"), tool_result("a", "x"))
    run(capsys, "--home", str(home), "session", "tally", "--transcript", str(t),
        "--session", "s1")
    _code, out, _err = run(capsys, "--home", str(home), "session", "prompt",
                           "--session", "s1")
    assert out.strip() == ""


def test_the_tally_records_what_was_noticed(tmp_path, capsys):
    """`candidates` and `unflagged` were dead fields — nothing wrote them, so
    `is_notable`'s unflagged branch and `render`'s were unreachable."""
    home = tmp_path / "home"
    t = a_session_with_a_finding(tmp_path)
    run(capsys, "--home", str(home), "session", "tally", "--transcript", str(t),
        "--session", "s1")
    entry = tally.read_tallies(home)[0]
    assert entry.candidates == 1
    assert entry.unflagged == 1


def test_a_session_that_flagged_is_not_reported_as_unflagged(tmp_path, capsys):
    home = tmp_path / "home"
    lines = []
    for i in range(2):
        lines += [tool_use("Bash", f"f{i}", command=f"sqlplus @a{i}.sql"),
                  tool_result(f"f{i}", "ORA-01000: cursors", is_error=True)]
    lines += [tool_use("Bash", "ok", command="sqlplus @fixed.sql"), tool_result("ok", "d"),
              tool_use("Bash", "fl", command='mneme flag "cursor cache exhausts at 296"'),
              tool_result("fl", "flagged")]
    t = transcript_file(tmp_path, *lines)
    run(capsys, "--home", str(home), "session", "tally", "--transcript", str(t),
        "--session", "s1")
    entry = tally.read_tallies(home)[0]
    assert entry.flags == 1
    assert entry.unflagged == 0
