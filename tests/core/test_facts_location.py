from mneme_core import units


def test_canonical_constant():
    assert units.FACTS_CANONICAL == "skills/knowledge-index/facts"


def test_prefers_canonical_when_present(tmp_path):
    (tmp_path / "skills" / "knowledge-index" / "facts").mkdir(parents=True)
    (tmp_path / "facts").mkdir()
    assert units.facts_dir(tmp_path) == tmp_path / "skills" / "knowledge-index" / "facts"


def test_reads_fall_back_to_legacy_while_writes_stay_canonical(tmp_path):
    """`facts_dir` is READ resolution — it still finds a pre-0.5 top-level `facts/`.

    Writes no longer follow it: a legacy repo keeps serving its files to every reader,
    and the next new topic still lands in the canonical directory (which is what makes
    the migration a one-way street rather than a layout mneme keeps accommodating).
    """
    legacy = tmp_path / "facts"
    legacy.mkdir()
    (legacy / "a.md").write_text("---\ntopic: a\n---\n", encoding="utf-8")
    assert units.facts_dir(tmp_path) == legacy
    assert [f.name for f in units.fact_files(tmp_path)] == ["a.md"]
    assert units.facts_write_dir(tmp_path) == tmp_path / units.FACTS_CANONICAL


def test_defaults_to_canonical_when_no_layout_exists(tmp_path):
    assert units.facts_dir(tmp_path) == tmp_path / "skills" / "knowledge-index" / "facts"
    assert units.facts_write_dir(tmp_path) == tmp_path / "skills" / "knowledge-index" / "facts"
