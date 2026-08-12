import json

from mneme_core import templates


SUBS = dict(
    name="acme-knowledge",
    description="Institutional knowledge for the Acme widget platform",
    owner="acme-maintainers",
    sensitivity="internal",
    mode="pr",
)


def test_plugin_json_renders_valid_json():
    data = json.loads(templates.render(templates.PLUGIN_JSON, **SUBS))
    assert data["name"] == "acme-knowledge"
    assert data["version"] == "0.1.0"
    assert data["description"] == SUBS["description"]


def test_marketplace_json_renders_valid_json():
    data = json.loads(templates.render(templates.MARKETPLACE_JSON, **SUBS))
    assert data["name"] == "acme-knowledge"
    assert data["owner"]["name"] == "acme-maintainers"
    assert data["plugins"][0]["source"] == "./"


def test_mneme_md_carries_scope_and_sensitivity():
    text = templates.render(templates.MNEME_MD, **SUBS)
    assert "## Scope statement" in text
    assert "internal" in text
    assert SUBS["description"] in text
    assert "## What does NOT belong here" in text


def test_codeowners_has_owner():
    text = templates.render(templates.CODEOWNERS, **SUBS)
    assert "* @acme-maintainers" in text


def test_contributing_has_rubric_and_ai_policy():
    text = templates.render(templates.CONTRIBUTING_MD, **SUBS)
    assert "verified" in text.lower()
    assert "unreviewed" in text.lower()


def test_workflows_reference_mneme_tooling():
    assert "bin/mneme lint" in templates.VALIDATE_YML
    assert "plugin.json" in templates.RELEASE_YML


def test_index_skill_renders_lintable_frontmatter():
    text = templates.render(templates.INDEX_SKILL_MD, **SUBS)
    assert text.startswith("---\nname: knowledge-index\n")
    assert "description:" in text


def test_render_rejects_missing_substitution():
    import pytest

    with pytest.raises(KeyError):
        templates.render(templates.PLUGIN_JSON, name="only-name")
