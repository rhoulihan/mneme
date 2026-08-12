from mneme_core import routing


def test_finds_marker_in_cwd(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "MNEME.md").write_text("# scope\n", encoding="utf-8")
    assert routing.find_knowledge_repo(kb) == kb.resolve()


def test_finds_marker_in_ancestor(tmp_path):
    kb = tmp_path / "kb"
    deep = kb / "facts" / "sub"
    deep.mkdir(parents=True)
    (kb / "MNEME.md").write_text("# scope\n", encoding="utf-8")
    assert routing.find_knowledge_repo(deep) == kb.resolve()


def test_nearest_marker_wins(tmp_path):
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    (outer / "MNEME.md").write_text("# outer\n", encoding="utf-8")
    (inner / "MNEME.md").write_text("# inner\n", encoding="utf-8")
    assert routing.find_knowledge_repo(inner) == inner.resolve()


def test_none_without_marker(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert routing.find_knowledge_repo(d) is None


def test_max_depth_bounds_the_walk(tmp_path):
    kb = tmp_path / "kb"
    deep = kb
    for i in range(5):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    (kb / "MNEME.md").write_text("# scope\n", encoding="utf-8")
    assert routing.find_knowledge_repo(deep, max_depth=3) is None
    assert routing.find_knowledge_repo(deep, max_depth=10) == kb.resolve()


def test_missing_cwd_is_silent(tmp_path):
    assert routing.find_knowledge_repo(tmp_path / "does-not-exist") is None
