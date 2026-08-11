from pathlib import Path

from mneme_core import paths


def test_mneme_home_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MNEME_HOME", str(tmp_path / "custom"))
    assert paths.mneme_home() == tmp_path / "custom"


def test_mneme_home_default(monkeypatch):
    monkeypatch.delenv("MNEME_HOME", raising=False)
    assert paths.mneme_home() == Path.home() / ".mneme"


def test_layout_paths_derive_from_home(tmp_path):
    assert paths.staging_dir(tmp_path) == tmp_path / "staging"
    assert paths.quarantine_dir(tmp_path) == tmp_path / "staging" / "quarantine"
    assert paths.repos_dir(tmp_path) == tmp_path / "repos"
    assert paths.logs_dir(tmp_path) == tmp_path / "logs"
    assert paths.registry_path(tmp_path) == tmp_path / "registry.json"
    assert paths.declined_path(tmp_path) == tmp_path / "declined.jsonl"
    assert paths.flags_path(tmp_path) == tmp_path / "staging" / "flags.jsonl"
    assert paths.db_path(tmp_path) == tmp_path / "mneme.db"


def test_ensure_layout_creates_dirs_and_is_idempotent(tmp_path):
    home = tmp_path / "m"
    returned = paths.ensure_layout(home)
    assert returned == home
    for d in (
        home,
        paths.staging_dir(home),
        paths.quarantine_dir(home),
        paths.repos_dir(home),
        paths.logs_dir(home),
    ):
        assert d.is_dir()
    paths.ensure_layout(home)  # second call must not raise
