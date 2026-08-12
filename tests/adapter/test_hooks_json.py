import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load():
    return json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))


def test_events_present():
    hooks = load()["hooks"]
    assert set(hooks) == {"SessionStart", "Stop", "PreCompact"}


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
