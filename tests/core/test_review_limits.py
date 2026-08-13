"""Untrusted PR text is bounded work (spec §7.8).

`mneme review triage` is the precondition check the maintainer skill runs first, and every
byte it parses is chosen by a contributor. Two unbounded paths met here: the bullet grammar
paired a lazy `.+?` with a repeated tag group — quadratic, measured at 1.2 s for a 20 KB
line and 76 s for 160 KB, so a few MB on one line stalled the whole command — and the
parser applied it to lines of any length. The grammar is linear now, and a line no human
wrote is reported as skipped instead of parsed.
"""
import time

from mneme_core import review, units
from mneme_core.errors import MnemeError

HEADER = "+++ b/skills/knowledge-index/facts/deploys.md\n@@ -0,0 +1,1 @@\n"


def hostile_bullet(kb):
    """A bullet the old grammar spent minutes backtracking over."""
    return "- [gotcha] x" + " #tag" * (kb * 1024 // 5) + "!"


def test_bullet_grammar_is_linear_in_line_length():
    line = hostile_bullet(160)
    assert len(line) > 160_000

    start = time.monotonic()
    try:
        units.parse_bullet_line(line, 1)
    except MnemeError:
        pass
    elapsed = time.monotonic() - start

    # The old grammar took ~76 s on this input; linear parsing is milliseconds. The bound
    # is loose enough for a loaded machine and still an order of magnitude under the bug.
    assert elapsed < 5.0, f"bullet parsing took {elapsed:.1f}s"


def test_an_oversized_bullet_is_skipped_rather_than_parsed():
    diff = HEADER + "+" + hostile_bullet(40) + "\n"

    start = time.monotonic()
    facts, skipped = review.parse_added_facts(1, diff)
    elapsed = time.monotonic() - start

    assert facts == []
    assert len(skipped) == 1
    assert "cap" in skipped[0] and "characters" in skipped[0]
    assert elapsed < 5.0, f"triage parsing took {elapsed:.1f}s"


def test_a_normal_bullet_is_nowhere_near_the_cap():
    line = "- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-12)"
    facts, skipped = review.parse_added_facts(1, HEADER + "+" + line + "\n")

    assert skipped == []
    assert [f.text for f in facts] == ["Deploys fail when the LB caches dead targets"]
    assert len(line) < review._MAX_BULLET_LINE


def test_many_tags_still_parse_when_the_line_is_ordinary():
    """The cap is on length, not on tag count — a heavily tagged bullet is still knowledge."""
    line = "- [gotcha] Deploys fail" + "".join(f" #t{i}" for i in range(40))
    bullet = units.parse_bullet_line(line, 1)

    assert bullet.text == "Deploys fail"
    assert len(bullet.tags) == 40
