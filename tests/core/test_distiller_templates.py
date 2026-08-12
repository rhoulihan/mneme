from mneme_core import templates


def test_noticing_brief_contents():
    text = templates.NOTICING_BRIEF
    assert "mneme flag" in text
    assert "knowledge-issue" in text
    assert "one line" in text.lower()


def test_distiller_prompt_renders_and_carries_contract():
    text = templates.render(
        templates.DISTILLER_PROMPT,
        scopes="- acme-knowledge [internal/pr]: Widget platform operations.",
        flags='{"kind": "golden-path", "text": "solved the deploy race"}',
        transcript_path="/tmp/session.jsonl",
    )
    assert "acme-knowledge" in text
    assert "solved the deploy race" in text
    assert "/tmp/session.jsonl" in text
    for token in (
        '"proposals"', '"type"', '"edit"', '"target"', '"confidence"', '"rationale"',
        '"name"', '"description"', '"procedure"', '"failure_pattern"',
        '"topic"', '"category"', '"text"', '"tags"', '"target_unit"',
    ):
        assert token in text, token
    assert "failure pattern" in text.lower()
    assert "unassigned" in text
