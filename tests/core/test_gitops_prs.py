import json
import os
import stat

import pytest

from mneme_core import gitops
from mneme_core.errors import MnemeError


def shim_gh(tmp_path, monkeypatch, pr_list_json, diffs):
    """diffs: dict number->unified diff text, written to files the shim cats."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    (bindir / "prlist.json").write_text(pr_list_json, encoding="utf-8")
    for n, diff in diffs.items():
        (bindir / f"diff{n}.txt").write_text(diff, encoding="utf-8")
    gh = bindir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *"pr list"*) cat "{bindir}/prlist.json" ;;\n'
        f'  *"pr diff"*) n=$(echo "$@" | grep -o "[0-9]*" | head -1); cat "{bindir}/diff$n.txt" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


PR_LIST = json.dumps(
    [
        {"number": 7, "title": "knowledge: harvest (2 units)", "headRefName": "mneme/harvest-x",
         "author": {"login": "alice"}, "url": "https://example.com/pr/7"},
        {"number": 9, "title": "add deploy gotchas", "headRefName": "feature/facts",
         "author": {"login": "bob"}, "url": "https://example.com/pr/9"},
    ]
)


def test_list_open_prs(tmp_path, monkeypatch):
    # The second half of the answer is whether the listing filled its limit; a two-PR
    # response to a request for a hundred did not (see tests/core/test_triage_accuracy.py).
    shim_gh(tmp_path, monkeypatch, PR_LIST, {})
    prs, truncated = gitops.list_open_prs(tmp_path)
    assert [p["number"] for p in prs] == [7, 9]
    assert prs[0]["author"] == "alice"
    assert truncated is False


def test_pr_diff(tmp_path, monkeypatch):
    shim_gh(tmp_path, monkeypatch, PR_LIST, {7: "+++ b/x\n+- [gotcha] hi (verified: 2026-08-12)\n"})
    assert "gotcha" in gitops.pr_diff(tmp_path, 7)


def test_missing_gh_is_clear(tmp_path, monkeypatch):
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(MnemeError) as exc:
        gitops.list_open_prs(tmp_path)
    assert "gh" in str(exc.value)
