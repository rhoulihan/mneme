"""The tally mneme already computes and throws away.

`hooks/scripts/distill-hook.sh` runs on Stop and contains

    "$ROOT/bin/mneme" distill pending >/dev/null 2>&1 || exit 0

and `distill pending` prints the pending flag count, exiting 1 when it is zero. So on
exactly the session the requirements doc describes -- a long agentic session that captured
nothing -- mneme computes the zero, redirects it to /dev/null, and exits silently. That is
RC4, "omission is silent", implemented.

A probe against Claude Code 2.1.278 established the constraint on fixing it: a `Stop` hook
cannot inject context and its stdout is discarded outright. So Stop WRITES the tally and a
later `SessionStart` speaks it. Seeing and speaking are different events.
"""
import json

import pytest

from mneme_core import flags, tally, transcript
from mneme_core.cli import main


# --- transcript fixtures -----------------------------------------------------


def line(**kw):
    return json.dumps(kw)


def assistant_tool_use(name, tool_id, sidechain=False, **inp):
    return line(
        type="assistant", isSidechain=sidechain, timestamp="2026-09-19T00:00:00Z",
        message={"content": [{"type": "tool_use", "name": name, "id": tool_id, "input": inp}]},
    )


def user_tool_result(tool_id, content="ok", is_error=False, sidechain=False):
    return line(
        type="user", isSidechain=sidechain, timestamp="2026-09-19T00:00:01Z",
        message={"content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": content,
             "is_error": is_error}
        ]},
    )


def assistant_text(text, sidechain=False):
    return line(
        type="assistant", isSidechain=sidechain, timestamp="2026-09-19T00:00:02Z",
        message={"content": [{"type": "text", "text": text}]},
    )


def write_transcript(tmp_path, *lines, name="t.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# --- the reader --------------------------------------------------------------


def test_it_counts_tool_calls(tmp_path):
    t = write_transcript(
        tmp_path,
        assistant_tool_use("Bash", "t1", command="false"),
        user_tool_result("t1", is_error=True),
        assistant_tool_use("Bash", "t2", command="echo hello"),
        user_tool_result("t2"),
        assistant_text("done"),
    )
    events = transcript.read_events(t)
    assert transcript.tool_calls(events) == 2


def test_a_tool_result_is_paired_to_its_call_and_carries_its_error(tmp_path):
    """`is_error` on the result is the fail->success signal T2 needs, and the only way to
    know which call it belongs to is `tool_use_id`."""
    t = write_transcript(
        tmp_path,
        assistant_tool_use("Bash", "t1", command="false"),
        user_tool_result("t1", content="Exit code 1", is_error=True),
        assistant_tool_use("Bash", "t2", command="true"),
        user_tool_result("t2", content="", is_error=False),
    )
    events = transcript.read_events(t)
    outcomes = transcript.outcomes(events)
    assert [(o.tool_name, o.command, o.failed) for o in outcomes] == [
        ("Bash", "false", True),
        ("Bash", "true", False),
    ]


def test_subagent_tool_calls_are_counted_from_their_own_files(tmp_path):
    """Claude Code does NOT mark delegated turns in the main transcript.

    This test previously asserted on `isSidechain`, which appears in ZERO real transcripts
    on this machine -- subagent turns are written to `<stem>/subagents/agent-*.jsonl`
    instead. The old test passed against a field nothing sets, while the real behaviour
    was a silent undercount: one real session here showed 1,292 main calls and 724 more
    across 19 subagent files, a 36% shortfall in the number injected into the model's
    context -- and enough, on a heavily delegated session, to fall under MIN_TOOL_CALLS
    and suppress the report entirely.
    """
    main = write_transcript(
        tmp_path,
        assistant_tool_use("Bash", "t1", command="a"), user_tool_result("t1"),
    )
    subdir = tmp_path / main.stem / "subagents"
    subdir.mkdir(parents=True)
    (subdir / "agent-abc.jsonl").write_text(
        "\n".join([
            assistant_tool_use("Grep", "s1", pattern="x"), user_tool_result("s1"),
            assistant_tool_use("Read", "s2", file_path="/x"), user_tool_result("s2"),
        ]) + "\n", encoding="utf-8",
    )
    events = transcript.read_events(main)
    assert transcript.tool_calls(events) == 3, "delegated work was not counted"
    assert transcript.tool_calls(events, include_sidechain=False) == 1
    assert len(transcript.subagent_paths(main)) == 1


def test_a_session_with_no_subagent_directory_is_fine(tmp_path):
    main = write_transcript(tmp_path, assistant_tool_use("Bash", "t1", command="a"))
    assert transcript.subagent_paths(main) == []
    assert transcript.tool_calls(transcript.read_events(main)) == 1


def test_a_truncated_line_is_skipped_not_fatal(tmp_path):
    """A transcript is read while the session that writes it may still be running, so the
    last line can be half-written. One bad line must not lose the whole session."""
    p = tmp_path / "t.jsonl"
    p.write_text(
        assistant_tool_use("Bash", "t1", command="a") + "\n"
        + '{"type": "assistant", "message": {"content": [{"type": "tool_' + "\n",
        encoding="utf-8",
    )
    events = transcript.read_events(p)
    assert transcript.tool_calls(events) == 1


def test_a_missing_transcript_is_empty_not_an_error(tmp_path):
    assert transcript.read_events(tmp_path / "nope.jsonl") == []


def test_a_record_that_is_not_an_object_is_skipped(tmp_path):
    p = write_transcript(tmp_path, "[1, 2, 3]", '"a string"', "null",
                         assistant_tool_use("Bash", "t1", command="a"))
    assert transcript.tool_calls(transcript.read_events(p)) == 1


# --- the ledger --------------------------------------------------------------


def test_a_tally_round_trips(tmp_path):
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="s1", tool_calls=40, flags=0))
    got = tally.read_tallies(home)
    assert len(got) == 1
    assert got[0].session == "s1" and got[0].tool_calls == 40 and got[0].flags == 0


def test_the_ledger_is_bounded(tmp_path):
    """An append-only file nothing trims is the ledger-bloat vector the ingest-hardening
    work already had to close once."""
    home = tmp_path / "home"
    for i in range(tally.MAX_RECORDS + 25):
        tally.record(home, tally.SessionTally(session=f"s{i}", tool_calls=1, flags=1))
    got = tally.read_tallies(home)
    assert len(got) == tally.MAX_RECORDS
    assert got[-1].session == f"s{tally.MAX_RECORDS + 24}", "the newest record was trimmed"


def test_a_silent_session_is_not_notable(tmp_path):
    """N4: a session producing nothing should produce no noise. Three tool calls and no
    flags is someone asking a question, not a capture failure."""
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="s1", tool_calls=3, flags=0))
    assert tally.last_notable(home) is None


def test_a_busy_session_with_no_flags_is_notable(tmp_path):
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="s1", tool_calls=214, flags=0))
    notable = tally.last_notable(home)
    assert notable is not None and notable.session == "s1"


def test_a_busy_session_that_captured_something_is_not_notable(tmp_path):
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="s1", tool_calls=214, flags=6))
    assert tally.last_notable(home) is None


def test_the_current_session_is_never_reported_to_itself(tmp_path):
    """The brief is injected at SessionStart, which is the start of a session whose own
    tally does not exist yet. Excluding by id keeps a resumed session from being told
    about itself."""
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="s1", tool_calls=99, flags=0))
    assert tally.last_notable(home, exclude="s1") is None


def test_only_the_most_recent_notable_session_is_reported(tmp_path):
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="old", tool_calls=99, flags=0))
    tally.record(home, tally.SessionTally(session="new", tool_calls=50, flags=0))
    assert tally.last_notable(home).session == "new"


# --- the CLI and the brief ---------------------------------------------------


def run(capsys, *argv):
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_the_tally_command_records_a_session(tmp_path, capsys):
    home = tmp_path / "home"
    t = write_transcript(
        tmp_path,
        *[l for i in range(3) for l in (
            assistant_tool_use("Bash", f"t{i}", command="x"), user_tool_result(f"t{i}"))],
    )
    code, _out, _err = run(
        capsys, "--home", str(home), "session", "tally",
        "--transcript", str(t), "--session", "s1",
    )
    assert code == 0
    got = tally.read_tallies(home)
    assert got[0].session == "s1" and got[0].tool_calls == 3 and got[0].flags == 0


def test_the_tally_counts_what_THIS_session_flagged(tmp_path, capsys):
    """Counted from the session's own record of flagging, not from the pending ledger.

    This test previously asserted `len(read_flags(home))` -- the count of every flag still
    PENDING across every session -- which is wrong in both directions and was the bug two
    reviews independently reproduced. It is replaced rather than deleted, because the
    property it was reaching for (the tally reflects capture) is the right one.
    """
    home = tmp_path / "home"
    t = write_transcript(
        tmp_path,
        assistant_tool_use("Bash", "t1", command='mneme flag "the webhook replays 72h"'),
        user_tool_result("t1"),
        assistant_tool_use("Bash", "t2", command="ls -la"),
        user_tool_result("t2"),
        assistant_tool_use("Bash", "t3", command='mneme flag --kind knowledge-issue "stale"'),
        user_tool_result("t3"),
    )
    run(capsys, "--home", str(home), "session", "tally", "--transcript", str(t),
        "--session", "s1")
    entry = tally.read_tallies(home)[0]
    assert entry.tool_calls == 3
    assert entry.flags == 2, "the session's own flag invocations were not counted"


def test_another_sessions_pending_flag_does_not_suppress_the_warning(tmp_path, capsys):
    """Reproduced by review: flags accumulate between distill runs, so ANY stale flag made
    `flags != 0` and `is_notable` returned False. That is RC4 -- omission is silent --
    reintroduced one layer above the line that caused it."""
    home = tmp_path / "home"
    flags.add_flag(home, "a flag left behind by an earlier session", session="other")
    t = write_transcript(
        tmp_path,
        *[l for i in range(30) for l in (
            assistant_tool_use("Bash", f"t{i}", command="ls"), user_tool_result(f"t{i}"))],
    )
    run(capsys, "--home", str(home), "session", "tally", "--transcript", str(t),
        "--session", "quiet")
    assert tally.read_tallies(home)[0].flags == 0
    assert tally.last_notable(home) is not None, "a stale flag silenced the report"


def test_a_session_whose_flags_were_distilled_still_counts_them(tmp_path, capsys):
    """The distiller runs `distill ingest --clear-flags`, which consumes the flags it
    ingested. Counting pending flags meant a session that flagged well and got distilled
    reported zero at the next turn's Stop -- a false omission report on the session that
    behaved correctly, which is worse than silence because it trains the reader to ignore
    the line."""
    home = tmp_path / "home"
    t = write_transcript(
        tmp_path,
        assistant_tool_use("Bash", "t1", command='mneme flag "something durable"'),
        user_tool_result("t1"),
        *[l for i in range(25) for l in (
            assistant_tool_use("Bash", f"x{i}", command="ls"), user_tool_result(f"x{i}"))],
    )
    assert flags.read_flags(home) == []  # the distiller already consumed everything
    run(capsys, "--home", str(home), "session", "tally", "--transcript", str(t),
        "--session", "productive")
    entry = tally.read_tallies(home)[0]
    assert entry.flags == 1
    assert tally.last_notable(home) is None, "a productive session was reported as silent"


def test_context_reports_a_session_that_captured_nothing(tmp_path, capsys):
    """R3: the explicit statement. This is the whole point -- the failure has to be loud."""
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="s1", tool_calls=214, flags=0))
    _code, out, _err = run(capsys, "--home", str(home), "context")
    assert "214" in out
    assert "0 flags" in out or "no flags" in out.lower()


def test_context_says_nothing_when_there_is_nothing_to_say(tmp_path, capsys):
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="s1", tool_calls=5, flags=0))
    _code, out, _err = run(capsys, "--home", str(home), "context")
    assert "5" not in out.split("Registered knowledge plugins")[0]


def test_context_still_carries_the_noticing_brief(tmp_path, capsys):
    """The tally is an addition to the brief, not a replacement for it."""
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="s1", tool_calls=214, flags=0))
    _code, out, _err = run(capsys, "--home", str(home), "context")
    assert "mneme flag" in out
    assert "Registered knowledge plugins" in out


def test_the_session_id_falls_back_to_the_environment(tmp_path, capsys, monkeypatch):
    """The Stop hook has `session_id` in its payload, but a hand-run `mneme session tally`
    does not. Every other mneme entry point reads `CLAUDE_SESSION_ID` for this, and the
    fallback path is the one a test that always passes --session never exercises."""
    monkeypatch.setenv("CLAUDE_SESSION_ID", "from-env")
    home = tmp_path / "home"
    t = write_transcript(tmp_path, assistant_tool_use("Bash", "t1", command="x"))
    code, _out, _err = run(capsys, "--home", str(home), "session", "tally",
                           "--transcript", str(t))
    assert code == 0
    assert tally.read_tallies(home)[0].session == "from-env"


def test_a_session_tallied_twice_is_updated_not_duplicated(tmp_path):
    """`Stop` fires when Claude finishes responding — that is once per TURN, not once per
    session. So the tally is rewritten many times in a long session, and appending would
    fill the ledger with one session and push every other out of it."""
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="s1", tool_calls=5, flags=0))
    tally.record(home, tally.SessionTally(session="s1", tool_calls=90, flags=0))
    got = tally.read_tallies(home)
    assert len(got) == 1
    assert got[0].tool_calls == 90, "the later, larger count did not win"


def test_an_updated_session_stays_the_most_recent(tmp_path):
    """Re-recording moves a session to the end, so `last_notable` reports the session that
    was actually active most recently rather than the one that first appeared."""
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="old", tool_calls=99, flags=0))
    tally.record(home, tally.SessionTally(session="new", tool_calls=99, flags=0))
    tally.record(home, tally.SessionTally(session="old", tool_calls=120, flags=0))
    assert tally.last_notable(home).session == "old"


# --- what the reviews found -------------------------------------------------


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\u0085"])
def test_a_raw_unicode_line_break_inside_a_record_does_not_delete_it(tmp_path, sep):
    """`str.splitlines()` breaks on ten characters. Node's `JSON.stringify` -- which
    writes these transcripts -- leaves U+2028, U+2029 and U+0085 RAW inside strings
    (verified against node directly). Splitting on them cuts a valid record in half, both
    halves fail to parse, and every tool call in that message vanishes from the count.
    This repo has fixed this exact class three times now."""
    # ensure_ascii=False on purpose: Python escapes these by default and Node does not,
    # so the default would test a file no harness ever writes.
    raw = json.dumps(
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "id": "t1",
             "input": {"command": f"echo a{sep}b"}}]}},
        ensure_ascii=False,
    )
    t = tmp_path / "t.jsonl"
    t.write_text(raw + "\n", encoding="utf-8")
    assert sep in t.read_text(encoding="utf-8"), "the separator was escaped by the fixture"
    assert transcript.tool_calls(transcript.read_events(t)) == 1


def test_deeply_nested_json_is_a_skipped_line_not_a_lost_session(tmp_path):
    """`json.loads` raises RecursionError -- not a ValueError -- past ~10k nesting.
    `proposals.py` guards the identical boundary for the identical reason."""
    p = tmp_path / "t.jsonl"
    p.write_text(
        "[" * 200_000 + "]" * 200_000 + "\n"
        + assistant_tool_use("Bash", "t1", command="a") + "\n",
        encoding="utf-8",
    )
    assert transcript.tool_calls(transcript.read_events(p)) == 1


def test_the_ledger_survives_concurrent_writers(tmp_path):
    """Stop is async and fires once per turn; PreCompact runs the same script; several
    sessions share one MNEME_HOME. Measured unlocked: 3 processes x 60 records left 10 of
    180, and what was lost was every OTHER session's record."""
    import multiprocessing

    home = tmp_path / "home"
    from mneme_core import paths
    paths.ensure_layout(home)

    def writer(home_str, lo, hi):
        from pathlib import Path as P

        from mneme_core import tally as tl

        for i in range(lo, hi):
            tl.record(P(home_str), tl.SessionTally(session=f"s{i}", tool_calls=1, flags=1))

    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=writer, args=(str(home), lo, lo + 40))
             for lo in (0, 40, 80)]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join(60)
    assert len(tally.read_tallies(home)) == 120


def test_a_corrupt_ledger_is_refused_rather_than_read_as_empty(tmp_path):
    """Lower stakes than the registry -- these records are advisory and regenerate -- but
    the failure mode is identical: read damage as emptiness and the next write deletes
    what survived."""
    from mneme_core import paths
    from mneme_core.errors import MnemeError

    home = tmp_path / "home"
    paths.ensure_layout(home)
    paths.sessions_path(home).write_text("this is not json\n", encoding="utf-8")
    with pytest.raises(MnemeError):
        tally.read_tallies(home)


def test_an_undecodable_ledger_does_not_take_the_brief_down_with_it(tmp_path, capsys):
    """`context` is what SessionStart runs, and the hook's `|| exit 0` would drop the
    noticing brief, the registry summary and the nudge if this raised."""
    from mneme_core import paths

    home = tmp_path / "home"
    paths.ensure_layout(home)
    paths.sessions_path(home).write_bytes(b"\xff\xfe\x00bad\n")
    code, out, _err = run(capsys, "--home", str(home), "context")
    assert code == 0
    assert "mneme noticing" in out


def test_a_tally_is_spoken_once(tmp_path, capsys):
    """Without this the same line is re-injected at every later SessionStart, where "Last
    session" is false from the second time on."""
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="s1", tool_calls=214, flags=0))
    _c, first, _e = run(capsys, "--home", str(home), "context", "--session", "a")
    _c, second, _e = run(capsys, "--home", str(home), "context", "--session", "b")
    assert "214 tool calls" in first
    assert "214 tool calls" not in second


def test_on_compact_a_session_is_told_its_own_numbers(tmp_path, capsys):
    """session_id is stable across compaction, so excluding the current session hid the
    live session's own 'lots of calls, no flags' behind an older session's -- at the exact
    moment its context was being discarded."""
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="old", tool_calls=99, flags=0))
    tally.record(home, tally.SessionTally(session="live", tool_calls=12477, flags=0))
    _c, out, _e = run(capsys, "--home", str(home), "context",
                      "--session", "live", "--source", "compact")
    assert "12477 tool calls" in out
    assert "This session so far" in out


def test_on_startup_the_current_session_is_still_excluded(tmp_path, capsys):
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="live", tool_calls=12477, flags=0))
    _c, out, _e = run(capsys, "--home", str(home), "context",
                      "--session", "live", "--source", "startup")
    assert "12477" not in out


def test_an_enormous_session_id_is_capped(tmp_path):
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="s" * 100_000, tool_calls=1, flags=1))
    assert len(tally.read_tallies(home)[0].session) == tally.MAX_SESSION_ID


def test_the_constants_are_pinned_to_their_values():
    """Both were mutation-tested and survived: `MIN_TOOL_CALLS` anywhere in (5, 200000)
    and `MAX_RECORDS` at ANY value left the suite green, because every test read the
    constant it was meant to pin. Literals are the only thing that holds a number."""
    assert tally.MIN_TOOL_CALLS == 20
    assert tally.MAX_RECORDS == 200


def test_the_threshold_holds_on_both_sides(tmp_path):
    home = tmp_path / "home"
    tally.record(home, tally.SessionTally(session="under", tool_calls=19, flags=0))
    assert tally.last_notable(home) is None
    tally.record(home, tally.SessionTally(session="at", tool_calls=20, flags=0))
    assert tally.last_notable(home).session == "at"


def test_an_interrupted_save_leaves_the_previous_ledger_intact(tmp_path, monkeypatch):
    """A Stop hook is async and is killed when its session exits. `write_text` truncates
    before it writes, so an interrupted save would leave an empty or half-written ledger —
    losing every session's tally, not just the one being written.

    The failure is injected between writing the temp file and swapping it in, which is
    exactly the window a kill would land in.
    """
    home = tmp_path / "home"
    for i in range(5):
        tally.record(home, tally.SessionTally(session=f"s{i}", tool_calls=10, flags=1))
    before = [t.session for t in tally.read_tallies(home)]

    def boom(*_a, **_k):
        raise KeyboardInterrupt("killed mid-save")

    monkeypatch.setattr("mneme_core.tally.os.replace", boom)
    with pytest.raises(KeyboardInterrupt):
        tally.record(home, tally.SessionTally(session="doomed", tool_calls=1, flags=0))

    assert [t.session for t in tally.read_tallies(home)] == before


@pytest.mark.parametrize(
    "command,expected",
    [
        ('mneme flag "the webhook replays for 72 hours"', 1),
        ('./bin/mneme flag "a thing"', 1),
        ('/usr/local/bin/mneme flag "a thing"', 1),
        ('MNEME_HOME=/tmp/h mneme flag "a thing"', 1),
        ('mneme flag --kind knowledge-issue "stale"', 1),
        # The false positive that silenced mneme's own sessions: a heredoc writing a
        # document that TALKS about flagging.
        ("cat > spec.md <<'EOF'\nrun `mneme flag \"x\"` when you learn something\nEOF", 0),
        ("cd /repo\ngrep -rn 'mneme flag' docs/", 0),
        ('echo "use mneme flag for this"', 0),
        # Accepted false negative, documented at the regex: erring toward over-reporting
        # an omission (noise) rather than silencing the report (the bug being fixed).
        ('cd /repo && mneme flag "a thing"', 0),
    ],
)
def test_only_a_real_invocation_counts_as_a_flag(tmp_path, command, expected):
    t = write_transcript(tmp_path, assistant_tool_use("Bash", "t1", command=command))
    assert transcript.flag_invocations(transcript.read_events(t)) == expected


def test_the_mcp_flag_tool_counts_too(tmp_path):
    """R6 replaces the shell path with a structured tool; the tally must follow it there."""
    t = write_transcript(tmp_path, assistant_tool_use("mcp__mneme__mneme_flag", "t1"))
    assert transcript.flag_invocations(transcript.read_events(t)) == 1
