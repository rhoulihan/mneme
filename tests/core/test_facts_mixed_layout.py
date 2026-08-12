"""A repo carrying BOTH fact layouts — knowledge on disk must never be invisible.

A 0.5 scaffold ships `skills/knowledge-index/facts/`, so the moment anyone files a fact at
the top level (as a hand edit, or from an older clone) the repo carries both layouts. When
the readers resolved to a single directory, those facts were committed, in git, listed by
`classify prepare` — and yet unsearchable, unindexed, unlinted, and uncounted by verify.
"""
from datetime import datetime, timedelta, timezone

from mneme_core import classify, harvest, lint, scaffold, units
from mneme_core.cli import main
from mneme_core.staging import Candidate, candidate_id
from mneme_index import build, db, search

CANONICAL_FACT = (
    "---\ntopic: deploys\n---\n"
    "- [gotcha] Deploys fail when the LB caches dead targets #deploy (verified: 2026-08-12)\n"
)
LEGACY_FACT = (
    "---\ntopic: pricing\n---\n"
    "- [constraint] Enterprise invoice terms are net 45 #billing (verified: 2026-08-12)\n"
)
CANONICAL_ID = "facts/deploys#deploys-fail-when-the-lb-caches"
LEGACY_ID = "facts/pricing#enterprise-invoice-terms-are-net-45"


def make_mixed(tmp_path, name="mixed-kb"):
    """A fresh scaffold (canonical layout) plus a fact filed at the top level."""
    home = tmp_path / "home"
    target = scaffold.create(home, name, owner="demo")
    (target / units.FACTS_CANONICAL / "deploys.md").write_text(CANONICAL_FACT, encoding="utf-8")
    legacy = target / "facts"
    legacy.mkdir()
    (legacy / "pricing.md").write_text(LEGACY_FACT, encoding="utf-8")
    return home, target


def test_facts_dirs_and_fact_files_cover_both_layouts(tmp_path):
    _home, target = make_mixed(tmp_path)
    assert units.facts_dirs(target) == [target / units.FACTS_CANONICAL, target / "facts"]
    assert [p.name for p in units.fact_files(target)] == ["deploys.md", "pricing.md"]
    # The write destination is still exactly one directory — reads widened, writes did not.
    assert units.facts_dir(target) == target / units.FACTS_CANONICAL


def test_index_and_search_reach_the_legacy_file(tmp_path):
    _home, target = make_mixed(tmp_path)
    conn = db.open_db(tmp_path / "i.db")
    stats = build.index_tree(conn, "mixed-kb", target)
    assert stats.facts == 2
    assert {r["id"] for r in search.list_facts(conn)} == {CANONICAL_ID, LEGACY_ID}
    hits = search.search(conn, "enterprise invoice")
    assert [h["id"] for h in hits] == [LEGACY_ID]
    conn.close()


def test_lint_reports_a_malformed_bullet_in_the_legacy_layout(tmp_path):
    _home, target = make_mixed(tmp_path)
    (target / "facts" / "pricing.md").write_text(
        "---\ntopic: pricing\n---\n- [bogus] Enterprise invoice terms (verified: 2026-08-12)\n",
        encoding="utf-8",
    )
    issues = lint.lint_repo(target)
    assert lint.has_errors(issues)
    assert any(i.code == "MN007" and i.path.endswith("pricing.md") for i in issues)


def test_verify_counts_units_from_both_layouts(tmp_path, capsys):
    home, target = make_mixed(tmp_path, "verify-mixed-kb")
    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    (target / units.FACTS_CANONICAL / "deploys.md").write_text(
        f"---\ntopic: deploys\n---\n- [gotcha] Deploys hang on cached targets #d (verified: {fresh})\n",
        encoding="utf-8",
    )
    (target / "facts" / "pricing.md").write_text(
        f"---\ntopic: pricing\n---\n- [constraint] Invoices are net 45 #b (verified: {fresh})\n",
        encoding="utf-8",
    )
    code = main(["--home", str(home), "verify", "verify-mixed-kb"])
    out = capsys.readouterr().out
    assert code == 0
    assert "stale 0 of 2" in out


def test_classify_bundle_and_the_index_agree(tmp_path):
    """The audit's symptom in one line: the librarian saw a fact retrieval could not."""
    home, target = make_mixed(tmp_path, "agree-kb")
    bundle_ids = {f["unit_id"] for f in classify.bundle(home, target)["facts"]}
    conn = db.open_db(tmp_path / "agree.db")
    build.index_tree(conn, "agree-kb", target)
    assert bundle_ids == {r["id"] for r in search.list_facts(conn)} == {CANONICAL_ID, LEGACY_ID}
    conn.close()


def test_regenerate_index_routes_to_both_layouts(tmp_path):
    _home, target = make_mixed(tmp_path, "regen-mixed-kb")
    scaffold.regenerate_index_skill(target, "regen-mixed-kb", "Mixed layout.")
    text = (target / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    assert "| deploys | facts/deploys.md | 1 |" in text
    assert "| pricing | facts/pricing.md | 1 |" in text
    assert "Topics: deploys, pricing" in text


def fact_candidate(target_name, body, **kw):
    return Candidate(
        id=candidate_id("fact", target_name, body), type="fact", edit=kw.pop("edit", "new"),
        target=target_name, body=body, **kw,
    )


def test_apply_fact_appends_to_the_layout_that_already_holds_the_topic(tmp_path):
    """Appending must never fork one topic into two files with one unit id between them."""
    _home, target = make_mixed(tmp_path, "apply-mixed-kb")
    cand = fact_candidate(
        "apply-mixed-kb",
        "- [constraint] Renewals are quoted annually #billing (verified: 2026-08-12)",
        topic="pricing",
    )
    harvest.apply_fact(target, cand)
    assert not (target / units.FACTS_CANONICAL / "pricing.md").exists()
    assert "quoted annually" in (target / "facts" / "pricing.md").read_text(encoding="utf-8")


def test_apply_fact_updates_a_bullet_in_the_legacy_layout(tmp_path):
    _home, target = make_mixed(tmp_path, "update-mixed-kb")
    cand = fact_candidate(
        "update-mixed-kb",
        "- [constraint] Enterprise invoice terms are net 45 days #billing (verified: 2026-08-12)",
        edit="update", target_unit=LEGACY_ID,
    )
    harvest.apply_fact(target, cand)
    assert "net 45 days" in (target / "facts" / "pricing.md").read_text(encoding="utf-8")


def test_apply_fact_creates_a_brand_new_topic_in_the_canonical_layout(tmp_path):
    _home, target = make_mixed(tmp_path, "new-topic-kb")
    cand = fact_candidate(
        "new-topic-kb",
        "- [decision] Search runs on SQLite FTS5 #index (verified: 2026-08-12)",
        topic="search-stack",
    )
    harvest.apply_fact(target, cand)
    assert (target / units.FACTS_CANONICAL / "search-stack.md").exists()
    assert not (target / "facts" / "search-stack.md").exists()


def test_find_fact_file_prefers_canonical_and_refuses_to_escape(tmp_path):
    _home, target = make_mixed(tmp_path, "find-kb")
    assert units.find_fact_file(target, "deploys") == target / units.FACTS_CANONICAL / "deploys.md"
    assert units.find_fact_file(target, "pricing") == target / "facts" / "pricing.md"
    assert units.find_fact_file(target, "absent") is None
    (target / "skills" / "pwned.md").write_text("owned", encoding="utf-8")
    assert units.find_fact_file(target, "../pwned") is None


def test_scaffold_docs_point_contributors_at_the_canonical_facts_dir(tmp_path):
    """The generated governance docs are what a contributor follows — they must not
    steer facts into a directory the readers would have to be told about twice."""
    home = tmp_path / "home"
    target = scaffold.create(home, "docs-kb", owner="demo")
    for rel in ("CONTRIBUTING.md", "AGENTS.md", "README.md"):
        text = (target / rel).read_text(encoding="utf-8")
        assert units.FACTS_CANONICAL in text, rel
        assert "in `facts/`" not in text, rel
