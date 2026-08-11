from mneme_core.units import content_hash, fact_unit_id, normalize_topic_key


def test_ascii_behavior_unchanged():
    assert normalize_topic_key("Staging DB resets nightly") == "staging-db-resets-nightly"


def test_cjk_text_gets_hash_fallback():
    key = normalize_topic_key("日本語のドキュメント検索")
    assert key == content_hash("日本語のドキュメント検索")[:8]
    assert key != ""


def test_distinct_cjk_bullets_get_distinct_ids():
    a = fact_unit_id("t", "日本語のドキュメント検索")
    b = fact_unit_id("t", "検索エンジンの構成")
    assert a != b
    assert not a.endswith("#")
