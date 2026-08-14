from mneme_core import harvest, units
from mneme_core.staging import Candidate, candidate_id

BULLET = "- [gotcha] A brand new topic bullet #new (verified: 2026-08-12)"

def as_plugin(root):
    """Make `root` a plugin repo — `facts_write_dir` reads the manifest to pick a layout."""
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "kb", "version": "0.1.0"}\n', encoding="utf-8"
    )
    return root



def cand(topic):
    return Candidate(
        id=candidate_id("fact", "t", BULLET), type="fact", edit="new",
        target="t", body=BULLET, topic=topic,
    )


def test_write_dir_is_always_canonical(tmp_path):
    as_plugin(tmp_path)
    assert units.facts_write_dir(tmp_path) == tmp_path / units.FACTS_CANONICAL
    (tmp_path / "facts").mkdir()
    assert units.facts_write_dir(tmp_path) == tmp_path / units.FACTS_CANONICAL
    (tmp_path / units.FACTS_CANONICAL).mkdir(parents=True)
    assert units.facts_write_dir(tmp_path) == tmp_path / units.FACTS_CANONICAL


def test_new_topic_lands_canonical_even_in_a_legacy_repo(tmp_path):
    as_plugin(tmp_path)
    legacy = tmp_path / "facts"
    legacy.mkdir()
    (legacy / "existing.md").write_text(
        "---\ntopic: existing\n---\n- [gotcha] old bullet #x (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    harvest.apply_fact(tmp_path, cand("brand-new"))
    assert (tmp_path / units.FACTS_CANONICAL / "brand-new.md").exists()
    assert not (legacy / "brand-new.md").exists()


def test_existing_legacy_topic_is_appended_not_forked(tmp_path):
    legacy = tmp_path / "facts"
    legacy.mkdir()
    (legacy / "existing.md").write_text(
        "---\ntopic: existing\n---\n- [gotcha] old bullet #x (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    harvest.apply_fact(tmp_path, cand("existing"))
    text = (legacy / "existing.md").read_text(encoding="utf-8")
    assert "old bullet" in text and "brand new topic bullet" in text
    assert not (tmp_path / units.FACTS_CANONICAL / "existing.md").exists()


def test_reads_still_resolve_legacy(tmp_path):
    legacy = tmp_path / "facts"
    legacy.mkdir()
    (legacy / "a.md").write_text("---\ntopic: a\n---\n", encoding="utf-8")
    assert units.facts_dir(tmp_path) == legacy
    assert [f.name for f in units.fact_files(tmp_path)] == ["a.md"]
