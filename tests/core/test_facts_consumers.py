from mneme_core import harvest, lint, scaffold, units
from mneme_core.staging import Candidate, candidate_id
from mneme_index import build, db

BULLET = "- [gotcha] Layout-agnostic fact #layout (verified: 2026-08-12)\n"
FACT_FILE = "---\ntopic: layout\n---\n" + BULLET


def make_layout(root, canonical):
    d = (root / units.FACTS_CANONICAL) if canonical else (root / "facts")
    d.mkdir(parents=True)
    (d / "layout.md").write_text(FACT_FILE, encoding="utf-8")
    return root


def index_ids(tmp_path, root, name):
    conn = db.open_db(tmp_path / f"{name}.db")
    build.index_tree(conn, name, root)
    ids = {r["id"] for r in conn.execute("SELECT id FROM units WHERE kind='fact'")}
    conn.close()
    return ids


def test_identical_ids_across_layouts(tmp_path):
    legacy = make_layout(tmp_path / "legacy", canonical=False)
    canonical = make_layout(tmp_path / "canon", canonical=True)
    assert index_ids(tmp_path, legacy, "l") == index_ids(tmp_path, canonical, "c")
    assert index_ids(tmp_path, legacy, "l2") == {"facts/layout#layout-agnostic-fact"}


def test_scaffold_has_no_top_level_facts(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "layout-kb", owner="demo")
    assert not (target / "facts").exists()
    assert (target / units.FACTS_CANONICAL / ".gitkeep").exists()


def fact_cand(body, topic):
    return Candidate(
        id=candidate_id("fact", "t", body), type="fact", edit="new",
        target="t", body=body, topic=topic,
    )


def test_apply_fact_appends_in_place_but_writes_new_topics_canonically(tmp_path):
    """A legacy repo is never forked mid-topic, and never grows a new legacy topic.

    Appending to a topic the repo already carries at the top level stays in that file —
    two files sharing one unit id would split the knowledge in half. A topic the repo
    does NOT yet have is a write, and writes are canonical: following the legacy layout
    here is what kept pre-0.5 repos legacy forever.
    """
    legacy = make_layout(tmp_path / "legacy", canonical=False)
    body = "- [constraint] Appended to the legacy topic #x (verified: 2026-08-12)"
    harvest.apply_fact(legacy, fact_cand(body, "layout"))
    text = (legacy / "facts" / "layout.md").read_text(encoding="utf-8")
    assert "Layout-agnostic fact" in text and "Appended to the legacy topic" in text
    assert not (legacy / units.FACTS_CANONICAL / "layout.md").exists()

    fresh = "- [constraint] A topic this repo did not have #x (verified: 2026-08-12)"
    harvest.apply_fact(legacy, fact_cand(fresh, "incoming"))
    assert (legacy / units.FACTS_CANONICAL / "incoming.md").exists()
    assert not (legacy / "facts" / "incoming.md").exists()


def test_legacy_repo_lints_clean(tmp_path):
    legacy = make_layout(tmp_path / "legacy", canonical=False)
    assert not lint.has_errors(lint.lint_repo(legacy))


def test_canonical_repo_lints_facts(tmp_path):
    canonical = make_layout(tmp_path / "canon", canonical=True)
    bad = canonical / units.FACTS_CANONICAL / "bad.md"
    bad.write_text("---\ntopic: bad\n---\n- [bogus] nope (verified: 2026-08-12)\n", encoding="utf-8")
    issues = lint.lint_repo(canonical)
    assert any(i.code == "MN007" for i in issues)
