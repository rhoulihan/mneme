from mneme_core import lint


def write_skill(root, dirname, frontmatter):
    d = root / "skills" / dirname
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(frontmatter, encoding="utf-8")
    return d


def codes(issues):
    return [i.code for i in issues]


def test_valid_skill_passes(tmp_path):
    d = write_skill(
        tmp_path,
        "deploy-widget",
        "---\nname: deploy-widget\ndescription: Use when deploying widgets\n---\nBody\n",
    )
    assert lint.lint_skill(d) == []


def test_missing_skill_md(tmp_path):
    d = tmp_path / "skills" / "empty-skill"
    d.mkdir(parents=True)
    issues = lint.lint_skill(d)
    assert codes(issues) == ["MN001"]


def test_name_mismatch_and_bad_name(tmp_path):
    d = write_skill(
        tmp_path, "deploy-widget", "---\nname: other-name\ndescription: d\n---\n"
    )
    assert "MN003" in codes(lint.lint_skill(d))
    d2 = write_skill(tmp_path, "bad", "---\nname: Bad_Name\ndescription: d\n---\n")
    assert "MN002" in codes(lint.lint_skill(d2))


def test_description_missing_and_too_long(tmp_path):
    d = write_skill(tmp_path, "no-desc", "---\nname: no-desc\n---\n")
    assert "MN004" in codes(lint.lint_skill(d))
    long_desc = "x" * 1025
    d2 = write_skill(
        tmp_path, "long-desc", f"---\nname: long-desc\ndescription: {long_desc}\n---\n"
    )
    assert "MN005" in codes(lint.lint_skill(d2))


def test_fact_file_lint(tmp_path):
    facts = tmp_path / "facts"
    facts.mkdir()
    f = facts / "staging-env.md"
    f.write_text(
        "---\ntopic: staging-env\n---\n"
        "- [constraint] DB resets nightly #db (verified: 2026-08-11)\n"
        "- [bogus] unknown category (verified: 2026-08-11)\n"
        "- [gotcha] no verified date\n"
        "- [broken no close\n",
        encoding="utf-8",
    )
    issues = lint.lint_fact_file(f)
    assert codes(issues) == ["MN007", "MN008", "MN006"]
    severities = {i.code: i.severity for i in issues}
    assert severities["MN008"] == "warn"
    assert severities["MN006"] == "error"
    # line numbers are absolute within the file (frontmatter offset applied)
    mn007 = next(i for i in issues if i.code == "MN007")
    assert mn007.line == 5


def test_fact_file_missing_topic(tmp_path):
    f = tmp_path / "notopic.md"
    f.write_text("- [gotcha] thing (verified: 2026-08-11)\n", encoding="utf-8")
    assert "MN009" in codes(lint.lint_fact_file(f))


def test_lint_repo_walks_both_tiers(tmp_path):
    write_skill(
        tmp_path,
        "good-skill",
        "---\nname: good-skill\ndescription: fine\n---\n",
    )
    facts = tmp_path / "facts"
    facts.mkdir()
    (facts / "t.md").write_text(
        "---\ntopic: t\n---\n- [bogus] x (verified: 2026-08-11)\n", encoding="utf-8"
    )
    issues = lint.lint_repo(tmp_path)
    assert codes(issues) == ["MN007"]
    assert lint.has_errors(issues)


def test_has_errors_ignores_warnings(tmp_path):
    facts = tmp_path / "facts"
    facts.mkdir()
    (facts / "t.md").write_text(
        "---\ntopic: t\n---\n- [gotcha] no date\n", encoding="utf-8"
    )
    issues = lint.lint_repo(tmp_path)
    assert codes(issues) == ["MN008"]
    assert not lint.has_errors(issues)
