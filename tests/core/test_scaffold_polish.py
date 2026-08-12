import json
import subprocess

from mneme_core import scaffold, templates


SUBS = dict(
    name="acme-knowledge",
    description="Institutional knowledge for the Acme widget platform",
    owner="acme-maintainers",
    sensitivity="internal",
    mode="pr",
)


def test_plugin_json_has_author():
    data = json.loads(templates.render_json(templates.PLUGIN_JSON, **SUBS))
    assert data["author"]["name"] == "acme-maintainers"


def test_marketplace_json_has_description():
    data = json.loads(templates.render_json(templates.MARKETPLACE_JSON, **SUBS))
    assert data["description"] == SUBS["description"]


def test_facts_dir_tracked_in_initial_commit(tmp_path):
    home = tmp_path / "home"
    target = scaffold.create(home, "tracked-knowledge", owner="demo")
    tracked = subprocess.run(
        ["git", "-C", str(target), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "facts/.gitkeep" in tracked
