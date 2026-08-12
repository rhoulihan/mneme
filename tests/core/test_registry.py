import json

import pytest

from mneme_core import paths, registry
from mneme_core.errors import MnemeError
from mneme_core.registry import Plugin


def make(name="acme-knowledge", **kw):
    defaults = dict(repo="git@github.com:acme/acme-knowledge.git", path="/tmp/x")
    defaults.update(kw)
    return Plugin(name=name, **defaults)


def test_load_empty_registry(tmp_path):
    assert registry.load_registry(tmp_path) == []


def test_add_and_get_round_trip(tmp_path):
    registry.add_plugin(tmp_path, make())
    loaded = registry.get_plugin(tmp_path, "acme-knowledge")
    assert loaded is not None
    assert loaded.repo == "git@github.com:acme/acme-knowledge.git"
    assert loaded.sensitivity == "internal"
    assert loaded.exclusions == []


def test_registry_file_shape(tmp_path):
    registry.add_plugin(tmp_path, make())
    data = json.loads(paths.registry_path(tmp_path).read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["plugins"][0]["name"] == "acme-knowledge"


def test_duplicate_name_rejected(tmp_path):
    registry.add_plugin(tmp_path, make())
    with pytest.raises(MnemeError):
        registry.add_plugin(tmp_path, make())


def test_remove_plugin(tmp_path):
    registry.add_plugin(tmp_path, make())
    registry.remove_plugin(tmp_path, "acme-knowledge")
    assert registry.get_plugin(tmp_path, "acme-knowledge") is None
    with pytest.raises(MnemeError):
        registry.remove_plugin(tmp_path, "acme-knowledge")


@pytest.mark.parametrize(
    "kw",
    [
        {"name": "Bad_Name"},
        {"sensitivity": "secret"},
        {"repo": ""},
    ],
)
def test_validation_rejects_bad_fields(tmp_path, kw):
    with pytest.raises(MnemeError):
        registry.add_plugin(tmp_path, make(**{"name": "ok-name", **kw}))
