import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load():
    return json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))


def test_events_present():
    hooks = load()["hooks"]
    # UserPromptSubmit added 2026-09-19: it and SessionStart are the only events that can
    # put text in front of the model (verified against Claude Code 2.1.278), and it is the
    # only one of the two that fires repeatedly during a session. Detection happens at
    # Stop; delivery has to happen here.
    assert set(hooks) == {"SessionStart", "Stop", "PreCompact", "UserPromptSubmit"}


def test_user_prompt_submit_wiring():
    group = load()["hooks"]["UserPromptSubmit"][0]
    handler = group["hooks"][0]
    assert handler["type"] == "command"
    assert handler["command"].endswith("/hooks/scripts/user-prompt-submit.sh")
    # Not async: an async hook cannot inject context, which is the entire job here.
    assert not handler.get("async", False)
    # It runs on the user's turn boundary, so it must be bounded well under the event's
    # 30-second ceiling.
    assert handler["timeout"] <= 15


def test_session_start_wiring():
    group = load()["hooks"]["SessionStart"][0]
    assert group["matcher"] == "startup|clear|compact|resume"
    handler = group["hooks"][0]
    assert handler["type"] == "command"
    assert "session-start.sh" in handler["command"]
    assert '"${CLAUDE_PLUGIN_ROOT}"' in handler["command"]
    assert handler.get("async") is not True


def test_distill_hooks_are_async():
    hooks = load()["hooks"]
    for event in ("Stop", "PreCompact"):
        handler = hooks[event][0]["hooks"][0]
        assert "distill-hook.sh" in handler["command"]
        assert handler["async"] is True
        assert handler["shell"] == "bash"
