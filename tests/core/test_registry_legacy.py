import json

from mneme_core import paths, registry
from mneme_core.registry import Plugin


def test_plugin_has_no_mode_field():
    assert "mode" not in {f.name for f in Plugin.__dataclass_fields__.values()}
    assert not hasattr(registry, "MODES")


def test_legacy_registry_with_mode_loads_and_resaves_clean(tmp_path):
    paths.ensure_layout(tmp_path)
    legacy = {
        "version": 1,
        "plugins": [
            {
                "name": "old-kb", "repo": "git@x:y.git", "path": "/tmp/old-kb",
                "mode": "commit", "sensitivity": "internal", "exclusions": [],
            }
        ],
    }
    paths.registry_path(tmp_path).write_text(json.dumps(legacy), encoding="utf-8")
    plugins = registry.load_registry(tmp_path)
    assert plugins[0].name == "old-kb"
    registry.save_registry(tmp_path, plugins)
    resaved = json.loads(paths.registry_path(tmp_path).read_text(encoding="utf-8"))
    assert "mode" not in resaved["plugins"][0]
