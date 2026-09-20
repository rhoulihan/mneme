"""The detector: specific candidates, never a generic reminder.

R2's own standard — a generic "flag anything useful?" gets dismissed, while "this turn
contained ORA-40454 and a subsequent success" does not. And its own constraint: precision
over recall, because a noisy detector is switched off within a day and a detector nobody
runs has worse recall than a quiet one.

Every rule here is mechanical. None asks a model anything.
"""
import json

import pytest

from mneme_core import detect, transcript


def tool_use(name, tool_id, **inp):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "id": tool_id, "input": inp}]}})


def tool_result(tool_id, content="", is_error=False):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": content,
         "is_error": is_error}]}})


def text(body):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": body}]}})


def events(tmp_path, *lines):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transcript.read_events(p)


def kinds(signals):
    return sorted({s.kind for s in signals})


# --- resolved errors ---------------------------------------------------------


def test_a_vendor_error_then_a_success_is_a_candidate(tmp_path):
    """T2's actual wording is "after >=2 PRIOR failures of the same shape", which this
    first implemented as one. On the replay corpus the one-failure rule fired 485 times in
    a single session -- mostly `python3` or `curl` being run again -- so the threshold is
    the spec's, not a guess."""
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="sqlplus @load.sql"),
        tool_result("a", "ORA-40454: path expression not a literal", is_error=True),
        tool_use("Bash", "a2", command="sqlplus @load-2.sql"),
        tool_result("a2", "ORA-40454: path expression not a literal", is_error=True),
        tool_use("Bash", "b", command="sqlplus @load-fixed.sql"),
        tool_result("b", "PL/SQL procedure successfully completed."),
    )
    signals = detect.resolved_errors(ev)
    assert len(signals) == 1
    assert signals[0].kind == "resolved-error"
    assert "ORA-40454" in signals[0].detail
    assert "ORA-40454" in signals[0].evidence


def test_a_failure_that_was_never_resolved_is_not_a_candidate(tmp_path):
    """Nothing was learned yet — the dead end is still a dead end."""
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="sqlplus @load.sql"),
        tool_result("a", "ORA-40454: bad", is_error=True),
    )
    assert detect.resolved_errors(ev) == []


def test_a_success_with_no_preceding_failure_is_not_a_candidate(tmp_path):
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="ls -la"),
        tool_result("a", "total 8"),
    )
    assert detect.resolved_errors(ev) == []


def test_an_unrelated_success_does_not_resolve_another_commands_failure(tmp_path):
    """Keyed on the command's shape: `git push` succeeding says nothing about why
    `sqlplus` failed."""
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="sqlplus @load.sql"),
        tool_result("a", "ORA-40454: bad", is_error=True),
        tool_use("Bash", "b", command="git status"),
        tool_result("b", "clean"),
    )
    assert detect.resolved_errors(ev) == []


def test_a_failure_with_no_vendor_code_is_not_a_candidate(tmp_path):
    """This previously asserted the opposite, and the corpus says the opposite is wrong.

    A named vendor code is what separates "I learned something about this system" from "my
    command had a typo". Without it, a failing-then-passing test suite -- the single most
    common event in any development session -- becomes a capture prompt.
    """
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="pytest tests/"),
        tool_result("a", "1 failed", is_error=True),
        tool_use("Bash", "a2", command="pytest tests/ -k x"),
        tool_result("a2", "1 failed", is_error=True),
        tool_use("Bash", "b", command="pytest tests/ -x"),
        tool_result("b", "12 passed"),
    )
    assert detect.resolved_errors(ev) == []


def test_the_same_error_on_the_same_command_is_reported_once(tmp_path):
    """Deduped by (shape, code): a loop that hits ORA-00942 forty times and then works is
    one thing learned, not forty prompts."""
    lines = []
    for i in range(3):
        lines += [tool_use("Bash", f"f{i}", command=f"sqlplus @a{i}.sql"),
                  tool_result(f"f{i}", "ORA-00942: table or view does not exist",
                              is_error=True)]
    lines += [tool_use("Bash", "ok", command="sqlplus @fixed.sql"),
              tool_result("ok", "done")]
    for i in range(3):
        lines += [tool_use("Bash", f"g{i}", command=f"sqlplus @b{i}.sql"),
                  tool_result(f"g{i}", "ORA-00942: table or view does not exist",
                              is_error=True)]
    lines += [tool_use("Bash", "ok2", command="sqlplus @fixed2.sql"),
              tool_result("ok2", "done")]
    assert len(detect.resolved_errors(events(tmp_path, *lines))) == 1


# --- retries -----------------------------------------------------------------


def test_the_same_command_tried_several_ways_is_a_candidate(tmp_path):
    ev = events(
        tmp_path,
        *[l for i, args in enumerate(("-i lo", "-i any", "-i eth0"))
          for l in (tool_use("Bash", f"t{i}", command=f"tcpdump {args}"),
                    tool_result(f"t{i}", ""))],
    )
    signals = detect.retried_commands(ev)
    assert len(signals) == 1
    assert signals[0].evidence.startswith("tcpdump")
    assert "3 distinct" in signals[0].detail


def test_the_identical_command_repeated_is_not_a_retry(tmp_path):
    """Re-running the same thing unchanged is polling, not working something out."""
    ev = events(
        tmp_path,
        *[l for i in range(5)
          for l in (tool_use("Bash", f"t{i}", command="docker ps"),
                    tool_result(f"t{i}", ""))],
    )
    assert detect.retried_commands(ev) == []


def test_two_attempts_are_below_the_threshold(tmp_path):
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="tcpdump -i lo"), tool_result("a", ""),
        tool_use("Bash", "b", command="tcpdump -i any"), tool_result("b", ""),
    )
    assert detect.retried_commands(ev) == []


# --- surprise and measurement ------------------------------------------------


def test_negation_of_expectation_with_evidence_is_a_candidate(tmp_path):
    ev = events(tmp_path, text(
        "The scram line is dead code: pg_hba is first-match-wins and initdb "
        "silently writes its own trust line at 127.0.0.1/32 before the entrypoint runs."
    ))
    signals = detect.surprises(ev)
    assert len(signals) == 1
    assert "silently" in signals[0].detail


def test_conversational_filler_alone_is_not_a_candidate(tmp_path):
    """"Actually" with nothing measured or named beside it is how people talk. This is the
    precision guard — the difference between a detector people keep and one they mute."""
    ev = events(tmp_path, text("Actually, let me check that first. It turns out I was right."))
    assert detect.surprises(ev) == []


def test_a_measured_boundary_is_a_candidate(tmp_path):
    ev = events(tmp_path, text(
        "Crossing the 8000 byte inline limit moves the value out of line."
    ))
    signals = detect.measured_bounds(ev)
    assert len(signals) == 1
    assert "8000" in signals[0].evidence


def test_a_number_without_a_boundary_is_not_a_candidate(tmp_path):
    ev = events(tmp_path, text("The run took 42 seconds and produced 100 rows."))
    assert detect.measured_bounds(ev) == []


def test_tool_output_prose_is_not_mined_for_surprise(tmp_path):
    """These words are signal in an explanation and noise in output — a log line saying
    "silently" is not a finding."""
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="cat log"),
        tool_result("a", "WARN: silently dropped 5 rows, actually 6, limit 100 rows"),
    )
    assert detect.surprises(ev) == []
    assert detect.measured_bounds(ev) == []


# --- the whole thing ---------------------------------------------------------


def test_a_quiet_session_produces_no_candidates(tmp_path):
    """R2's acceptance criterion 2, and N4: no findings, no noise."""
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="ls"), tool_result("a", "a.txt"),
        tool_use("Read", "b", file_path="/x"), tool_result("b", "contents"),
        text("Here is the file you asked for."),
    )
    assert detect.detect(ev) == []


def test_a_session_with_findings_produces_specific_candidates(tmp_path):
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="sqlplus @load.sql"),
        tool_result("a", "ORA-40454: path expression not a literal", is_error=True),
        tool_use("Bash", "a2", command="sqlplus @load-2.sql"),
        tool_result("a2", "ORA-40454: path expression not a literal", is_error=True),
        tool_use("Bash", "b", command="sqlplus @load-fixed.sql"),
        tool_result("b", "completed"),
        text("It turns out JSON_TRANSFORM prepares cleanly and fails at execute, "
             "so per-field caching exhausts cursors past 296 distinct fields."),
    )
    signals = detect.detect(ev, rules=detect.ALL_RULES)
    assert "resolved-error" in kinds(signals)
    assert any("ORA-40454" in s.evidence or "ORA-40454" in s.detail for s in signals)
    assert all(s.evidence for s in signals), "a candidate with no evidence is a reminder"


def test_evidence_is_bounded(tmp_path):
    """Candidates are rendered into the model's context; an unbounded excerpt is the
    ledger-bloat vector this repo has capped everywhere else."""
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="run"),
        tool_result("a", "ORA-40454 " + "x" * 100_000, is_error=True),
        tool_use("Bash", "a2", command="run --again"),
        tool_result("a2", "ORA-40454 " + "x" * 100_000, is_error=True),
        tool_use("Bash", "b", command="run --fixed"),
        tool_result("b", "ok"),
    )
    for s in detect.detect(ev, rules=detect.ALL_RULES):
        assert len(s.evidence) <= detect.MAX_EVIDENCE


def test_only_the_measured_rule_is_on_by_default(tmp_path):
    """The other three produced 245 signals on one real session. R2's constraint is
    precision over recall, and a detector people mute has worse recall than a quiet one.
    Enabling them is a decision with evidence attached, not a default."""
    assert detect.DEFAULT_RULES == ("resolved-error",)
    ev = events(tmp_path, text(
        "It turns out the pg_hba file is first-match-wins, so crossing the 8000 byte "
        "limit silently does nothing."
    ))
    assert detect.detect(ev) == []
    assert detect.detect(ev, rules=detect.ALL_RULES) != []


def test_one_failure_then_a_success_is_not_enough(tmp_path):
    """The threshold is the spec's "after >=2 PRIOR failures", and it is load-bearing: at
    one failure this rule fired 485 times on a single real session."""
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="sqlplus @load.sql"),
        tool_result("a", "ORA-00942: table or view does not exist", is_error=True),
        tool_use("Bash", "b", command="sqlplus @fixed.sql"),
        tool_result("b", "done"),
    )
    assert detect.resolved_errors(ev) == []


def test_a_cd_prefix_does_not_become_the_commands_identity(tmp_path):
    """Nearly every command in a real session starts `cd /repo && …` or `cd /repo\\n…`.
    Without stripping it, every command shares the shape `cd`, the session collapses into
    one bucket, and unrelated failures pair with unrelated successes — 521 junk signals in
    the corpus, every one of them shaped `cd`."""
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="cd /repo && sqlplus @load.sql"),
        tool_result("a", "ORA-00942: table or view does not exist", is_error=True),
        tool_use("Bash", "b", command="cd /repo\nsqlplus @load2.sql"),
        tool_result("b", "ORA-00942: table or view does not exist", is_error=True),
        tool_use("Bash", "c", command="cd /repo && sqlplus @fixed.sql"),
        tool_result("c", "done"),
        # A different program that also failed twice must NOT be resolved by the above.
        tool_use("Bash", "d", command="cd /repo && psql -f x.sql"),
        tool_result("d", "ERRCODE_UNDEFINED_TABLE", is_error=True),
        tool_use("Bash", "e", command="cd /repo && psql -f y.sql"),
        tool_result("e", "ERRCODE_UNDEFINED_TABLE", is_error=True),
    )
    signals = detect.resolved_errors(ev)
    assert len(signals) == 1
    assert "sqlplus" in signals[0].detail, signals[0].detail
    assert "`cd`" not in signals[0].detail


def test_the_same_code_on_two_different_programs_is_two_findings(tmp_path):
    """Dedupe is on (shape, code) — the same errno from two different tools is two things
    learned, not one."""
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="sqlplus @a.sql"),
        tool_result("a", "ORA-00942", is_error=True),
        tool_use("Bash", "b", command="sqlplus @b.sql"),
        tool_result("b", "ORA-00942", is_error=True),
        tool_use("Bash", "c", command="sqlplus @ok.sql"),
        tool_result("c", "done"),
        tool_use("Bash", "d", command="python3 load.py"),
        tool_result("d", "ORA-00942", is_error=True),
        tool_use("Bash", "e", command="python3 load2.py"),
        tool_result("e", "ORA-00942", is_error=True),
        tool_use("Bash", "f", command="python3 ok.py"),
        tool_result("f", "done"),
    )
    assert len(detect.resolved_errors(ev)) == 2


@pytest.mark.parametrize("mover", ["cat", "sed", "echo", "grep", "git"])
def test_a_code_printed_by_a_text_mover_is_not_a_failure(tmp_path, mover):
    """`cat server.log` printing ORA-00942 says nothing about cat. Before this rule, the
    top of the delivery queue on the replay corpus was
    "ERRCODE_INTERNAL_ERROR then a success of `echo`"."""
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command=f"{mover} server.log"),
        tool_result("a", "... ORA-00942: table or view does not exist ..."),
        tool_use("Bash", "b", command=f"{mover} other.log"),
        tool_result("b", "... ORA-00942: table or view does not exist ..."),
        tool_use("Bash", "c", command=f"{mover} clean.log"),
        tool_result("c", "all fine"),
    )
    assert detect.resolved_errors(ev) == []


def test_a_code_in_the_output_of_a_real_command_IS_a_failure(tmp_path):
    """The inverse, and the reason the rule cannot key on exit status: only 83 of 5,095
    results in the replay corpus set `is_error`, because a database error arrives with
    exit code 0 — `docker exec ... sqlplus` succeeds while printing ORA-00942."""
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="docker exec db sqlplus @a.sql"),
        tool_result("a", "ORA-00942: table or view does not exist"),
        tool_use("Bash", "b", command="docker exec db sqlplus @b.sql"),
        tool_result("b", "ORA-00942: table or view does not exist"),
        tool_use("Bash", "c", command="docker exec db sqlplus @fixed.sql"),
        tool_result("c", "PL/SQL procedure successfully completed."),
    )
    signals = detect.resolved_errors(ev)
    assert len(signals) == 1
    assert "docker exec" in signals[0].detail


def test_a_bare_cd_identifies_nothing(tmp_path):
    ev = events(
        tmp_path,
        tool_use("Bash", "a", command="cd /repo"),
        tool_result("a", "ORA-00942", is_error=True),
        tool_use("Bash", "b", command="cd /other"),
        tool_result("b", "ORA-00942", is_error=True),
        tool_use("Bash", "c", command="cd /third"),
        tool_result("c", "ok"),
    )
    assert detect.resolved_errors(ev) == []


def test_the_hardest_won_finding_is_delivered_first(tmp_path):
    """Only a few candidates are shown per turn, so ordering decides what a user ever
    sees. Four failures before a fix is a better bet than two."""
    lines = []
    for i in range(2):
        lines += [tool_use("Bash", f"e{i}", command=f"psql -f a{i}.sql"),
                  tool_result(f"e{i}", "ERRCODE_UNDEFINED_TABLE", is_error=True)]
    lines += [tool_use("Bash", "e-ok", command="psql -f fixed.sql"),
              tool_result("e-ok", "done")]
    for i in range(5):
        lines += [tool_use("Bash", f"h{i}", command=f"sqlplus @b{i}.sql"),
                  tool_result(f"h{i}", "ORA-06550: line 1", is_error=True)]
    lines += [tool_use("Bash", "h-ok", command="sqlplus @good.sql"),
              tool_result("h-ok", "done")]
    signals = detect.resolved_errors(events(tmp_path, *lines))
    assert signals[0].detail.startswith("ORA-06550"), [s.detail for s in signals]
