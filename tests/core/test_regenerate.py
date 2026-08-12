from mneme_core import lint, scaffold, units


def test_regenerate_reflects_fact_topics(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "regen-knowledge")
    (target / "facts" / "staging-env.md").write_text(
        "---\ntopic: staging-env\n---\n"
        "- [constraint] DB resets nightly #db (verified: 2026-08-11)\n"
        "- [gotcha] API truncates batches #api (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    (target / "facts" / "billing.md").write_text(
        "---\ntopic: billing\n---\n"
        "- [decision] Invoices settle monthly #billing (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    scaffold.regenerate_index_skill(
        target, "regen-knowledge", "Knowledge for the regen test."
    )
    text = (target / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    assert "| billing | facts/billing.md | 1 |" in text
    assert "| staging-env | facts/staging-env.md | 2 |" in text
    assert text.index("billing |") < text.index("staging-env |")
    assert "Topics: billing, staging-env" in text


def test_regenerated_skill_stays_lint_clean(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "lint-knowledge")
    for i in range(40):
        (target / "facts" / f"topic-{i:02d}.md").write_text(
            f"---\ntopic: topic-{i:02d}\n---\n"
            f"- [reference] Reference number {i} #ref (verified: 2026-08-11)\n",
            encoding="utf-8",
        )
    scaffold.regenerate_index_skill(target, "lint-knowledge", "Many topics." )
    issues = lint.lint_repo(target)
    assert not lint.has_errors(issues)


def test_create_ships_regenerated_index(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "fresh-knowledge")
    text = (target / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    assert "| Topic | File | Bullets |" in text


def _index_description(target):
    text = (target / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    meta, _body = units.parse_frontmatter(text)
    return str(meta["description"])


def test_description_capped_with_no_facts(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "cap-knowledge")
    scaffold.regenerate_index_skill(target, "cap-knowledge", "x" * 950)
    assert not list((target / "facts").glob("*.md"))
    assert len(_index_description(target)) == lint.MAX_DESCRIPTION
    assert not lint.has_errors(lint.lint_repo(target))


def test_description_capped_with_facts(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "cap-facts-knowledge")
    (target / "facts" / "billing.md").write_text(
        "---\ntopic: billing\n---\n"
        "- [decision] Invoices settle monthly #billing (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    scaffold.regenerate_index_skill(target, "cap-facts-knowledge", "y" * 950)
    assert len(_index_description(target)) == lint.MAX_DESCRIPTION
    assert not lint.has_errors(lint.lint_repo(target))


def test_multiline_description_folds_to_one_line(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "fold-knowledge")
    scaffold.regenerate_index_skill(target, "fold-knowledge", "first line\nsecond line")
    assert "first line second line" in _index_description(target)
    assert not lint.has_errors(lint.lint_repo(target))


def test_unparseable_fact_file_listed_with_zero(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "tolerant-knowledge")
    (target / "facts" / "broken.md").write_text(
        "---\ntopic: broken\nno closing delim", encoding="utf-8"
    )
    scaffold.regenerate_index_skill(target, "tolerant-knowledge", "d")
    text = (target / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    assert "| broken | facts/broken.md | 0 |" in text
