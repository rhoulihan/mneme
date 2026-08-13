import re

from mneme_core import lint, scaffold, units


def test_regenerate_reflects_fact_topics(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "regen-knowledge")
    (target / units.FACTS_CANONICAL / "staging-env.md").write_text(
        "---\ntopic: staging-env\n---\n"
        "- [constraint] DB resets nightly #db (verified: 2026-08-11)\n"
        "- [gotcha] API truncates batches #api (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    (target / units.FACTS_CANONICAL / "billing.md").write_text(
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
    # The description carries a COUNT; the topic NAMES live in the body table above.
    assert "2 topics" in text
    assert "Topics: billing, staging-env" not in text


def test_regenerated_skill_stays_lint_clean(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "lint-knowledge")
    for i in range(40):
        (target / units.FACTS_CANONICAL / f"topic-{i:02d}.md").write_text(
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
    assert not list((target / units.FACTS_CANONICAL).glob("*.md"))
    assert len(_index_description(target)) == lint.MAX_DESCRIPTION
    assert not lint.has_errors(lint.lint_repo(target))


def test_description_capped_with_facts(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "cap-facts-knowledge")
    (target / units.FACTS_CANONICAL / "billing.md").write_text(
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
    (target / units.FACTS_CANONICAL / "broken.md").write_text(
        "---\ntopic: broken\nno closing delim", encoding="utf-8"
    )
    scaffold.regenerate_index_skill(target, "tolerant-knowledge", "d")
    text = (target / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    assert "| broken | facts/broken.md | 0 |" in text


def test_regenerate_reads_a_legacy_facts_layout(tmp_path):
    """A repo scaffolded before the move keeps its top-level `facts/` and still indexes."""
    home = tmp_path / "home"
    target = scaffold.create(home, "legacy-regen-knowledge")
    (target / units.FACTS_CANONICAL / ".gitkeep").unlink()
    (target / units.FACTS_CANONICAL).rmdir()
    legacy = target / "facts"
    legacy.mkdir()
    (legacy / "billing.md").write_text(
        "---\ntopic: billing\n---\n"
        "- [decision] Invoices settle monthly #billing (verified: 2026-08-11)\n",
        encoding="utf-8",
    )
    scaffold.regenerate_index_skill(target, "legacy-regen-knowledge", "Legacy layout.")
    text = (target / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    assert "| billing | facts/billing.md | 1 |" in text
    assert "1 topic," in text  # singular, and a count rather than the name
    assert not lint.has_errors(lint.lint_repo(target))


# --- Claude Code's real limit is 500 chars on a SKILL.md description ---------------
#
# mneme's own gate used to allow 1024 — twice the platform's limit — so a harvest could
# pass lint, pass CI, merge, and only then break the plugin at install time. That is the
# worst shape a gate can fail in: every check green and the artifact broken. Observed in
# practice on a real harvest into oracle-ai-dev (854 chars) and mneme's own knowledge repo
# (560), each needing a manual repair before the PR could merge.


def test_the_index_description_never_exceeds_the_platform_limit(tmp_path):
    """The description is O(1) in fact count, so this holds at any repo size.

    It used to carry `Topics: a, b, c…`, one entry per fact file, which grows without
    bound — any fixed budget is a cliff the repo eventually walks off. The topic NAMES
    live in the body table, which no reader loads until the skill is opened.
    """
    home = tmp_path / "home"
    target = scaffold.create(home, "growth-knowledge")
    scope = (
        "Widget platform operations at Acme: deploy paths, incident runbooks, and the "
        "constraints of the billing pipeline. Excludes customer data and the marketing site."
    )
    seen = set()
    for n in (0, 1, 3, 25, 200):
        for i in range(len(seen), n):
            topic = f"a-fairly-long-topic-name-number-{i:03d}"
            (target / units.FACTS_CANONICAL / f"{topic}.md").write_text(
                f"---\ntopic: {topic}\n---\n"
                f"- [reference] Reference number {i} #ref (verified: 2026-08-11)\n",
                encoding="utf-8",
            )
            seen.add(topic)
        scaffold.regenerate_index_skill(target, "growth-knowledge", scope)
        description = _index_description(target)
        assert len(description) <= 500, f"{n} topics -> {len(description)} chars"
        assert not lint.has_errors(lint.lint_repo(target)), n


def test_the_index_description_reports_a_topic_count_not_a_topic_list(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "count-knowledge")
    for topic in ("billing", "staging-env", "deploys"):
        (target / units.FACTS_CANONICAL / f"{topic}.md").write_text(
            f"---\ntopic: {topic}\n---\n"
            f"- [decision] Something about {topic} #x (verified: 2026-08-11)\n",
            encoding="utf-8",
        )
    scaffold.regenerate_index_skill(target, "count-knowledge", "Knowledge for the count test.")

    description = _index_description(target)
    assert "3 topics" in description  # the count, not the names
    assert "billing" not in description  # the names live in the body, not the description
    # ...and the body still routes to every one of them.
    text = (target / "skills" / "knowledge-index" / "SKILL.md").read_text(encoding="utf-8")
    for topic in ("billing", "staging-env", "deploys"):
        assert f"| {topic} | facts/{topic}.md | 1 |" in text


def test_a_long_scope_statement_is_trimmed_on_a_word_boundary(tmp_path):
    """Trimming has to leave a readable sentence, not a severed word.

    The old cap was a bare `rendered[:1024]`, which could slice mid-token and leave a
    half-written topic name that routes nowhere, with nothing saying anything was dropped.
    """
    home = tmp_path / "home"
    target = scaffold.create(home, "trim-knowledge")
    scope = " ".join(f"word{i:03d}" for i in range(200))
    scaffold.regenerate_index_skill(target, "trim-knowledge", scope)

    description = _index_description(target)
    assert len(description) <= 500
    assert "word" in description  # some of the scope survived
    # every surviving wordNNN token is whole
    for token in re.findall(r"word\d*", description):
        assert re.fullmatch(r"word\d{3}", token), f"severed token: {token!r}"
    assert not lint.has_errors(lint.lint_repo(target))


def test_zero_facts_says_so_rather_than_claiming_a_count(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "empty-knowledge")
    scaffold.regenerate_index_skill(target, "empty-knowledge", "Nothing here yet.")

    description = _index_description(target)
    assert len(description) <= 500
    assert "0 topics" not in description
    assert not lint.has_errors(lint.lint_repo(target))
