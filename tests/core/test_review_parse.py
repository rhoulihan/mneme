from mneme_core import review

DIFF = """diff --git a/skills/knowledge-index/facts/deploys.md b/skills/knowledge-index/facts/deploys.md
--- a/skills/knowledge-index/facts/deploys.md
+++ b/skills/knowledge-index/facts/deploys.md
@@ -2,3 +2,5 @@
 - [gotcha] existing bullet #x (verified: 2026-08-11)
+- [constraint] New constraint from the PR #deploy (verified: 2026-08-12)
+- [broken no close
diff --git a/facts/legacy.md b/facts/legacy.md
--- /dev/null
+++ b/facts/legacy.md
@@ -0,0 +1,2 @@
+---
+- [gotcha] Legacy-location addition #old (verified: 2026-08-12)
diff --git a/skills/new-skill/SKILL.md b/skills/new-skill/SKILL.md
--- /dev/null
+++ b/skills/new-skill/SKILL.md
@@ -0,0 +1,2 @@
+---
+name: new-skill
diff --git a/README.md b/README.md
+++ b/README.md
@@ -1 +1,2 @@
+- [gotcha] not a fact file, must be ignored (verified: 2026-08-12)
"""


def test_parse_added_facts():
    facts, skipped = review.parse_added_facts(7, DIFF)
    texts = [f.text for f in facts]
    assert "New constraint from the PR" in texts
    assert "Legacy-location addition" in texts
    assert all(f.pr == 7 for f in facts)
    assert not any("not a fact file" in t for t in texts)
    got = next(f for f in facts if f.stem == "deploys")
    assert got.unit_id == "facts/deploys#new-constraint-from-the-pr"
    assert got.category == "constraint"
    assert len(skipped) == 1 and "broken" in skipped[0]


def test_parse_added_skills():
    skills = review.parse_added_skills(7, DIFF)
    assert skills == [{"pr": 7, "file": "skills/new-skill/SKILL.md", "name": "new-skill"}]


def test_crlf_and_garbage_tolerated():
    facts, skipped = review.parse_added_facts(3, DIFF.replace("\n", "\r\n") + "\x00binary")
    assert any(f.text == "New constraint from the PR" for f in facts)
