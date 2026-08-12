from mneme_core import units


def test_canonical_constant():
    assert units.FACTS_CANONICAL == "skills/knowledge-index/facts"


def test_prefers_canonical_when_present(tmp_path):
    (tmp_path / "skills" / "knowledge-index" / "facts").mkdir(parents=True)
    (tmp_path / "facts").mkdir()
    assert units.facts_dir(tmp_path) == tmp_path / "skills" / "knowledge-index" / "facts"


def test_falls_back_to_legacy(tmp_path):
    (tmp_path / "facts").mkdir()
    assert units.facts_dir(tmp_path) == tmp_path / "facts"


def test_defaults_to_canonical_for_creation(tmp_path):
    assert units.facts_dir(tmp_path) == tmp_path / "skills" / "knowledge-index" / "facts"
