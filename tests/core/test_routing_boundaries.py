from pathlib import Path

from mneme_core import registry, routing
from mneme_core.registry import Plugin
from mneme_core.routing import Scope


def scope(name="t", sensitivity="internal", path="/x"):
    return Scope(name=name, sensitivity=sensitivity, path=path, statement="")


def test_less_restricted_target_warns():
    msg = routing.boundary_warning("restricted", scope(name="pub-kb", sensitivity="public"))
    assert "pub-kb" in msg and "public" in msg and "restricted" in msg


def test_equal_or_more_restricted_is_silent():
    assert routing.boundary_warning("internal", scope(sensitivity="internal")) == ""
    assert routing.boundary_warning("internal", scope(sensitivity="restricted")) == ""
    assert routing.boundary_warning("public", scope(sensitivity="internal")) == ""


def test_unknown_sensitivity_ranks_internal():
    assert routing.boundary_warning("wat", scope(sensitivity="internal")) == ""
    assert routing.boundary_warning("restricted", scope(sensitivity="wat")) != ""


def test_plugin_for_path_deepest_match(tmp_path):
    home = tmp_path / "home"
    outer = tmp_path / "repos" / "outer"
    inner = outer / "nested" / "inner"
    inner.mkdir(parents=True)
    registry.add_plugin(home, Plugin(name="outer-kb", repo="r", path=str(outer)))
    registry.add_plugin(home, Plugin(name="inner-kb", repo="r", path=str(inner)))
    hit = routing.plugin_for_path(home, inner / "facts")
    assert hit is not None and hit.name == "inner-kb"
    hit = routing.plugin_for_path(home, outer / "skills")
    assert hit is not None and hit.name == "outer-kb"
    assert routing.plugin_for_path(home, tmp_path / "elsewhere") is None
