"""What adoption reads before it asks anything — the scope interview's raw material.

"What should this repo's scope be?" is a question almost nobody can answer cold, and the
answer is the routing prompt: mneme matches every candidate fact against it, so a vague one
quietly steals candidates from every sibling scope. Asking from nothing produced vague ones.

So adoption proposes and the user corrects. This bundle is what the proposal is built from
— the repo's own README, manifests, shape, and history, plus the scopes already registered
so the draft can say where the boundary falls. Everything in it is repo content nobody on
mneme's side wrote, so it travels inside the same standing-rule sandwich the classify and
review bundles use.
"""
import json
import subprocess

import pytest

from mneme_core import gitops, registry, scaffold, templates
from mneme_core.errors import MnemeError
from mneme_core.cli import main
from mneme_core.registry import Plugin


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def app_repo(tmp_path, home, name="payments-service", *, files=(), commits=()):
    repo = tmp_path / name
    repo.mkdir(parents=True)
    for rel, content in files:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    gitops.git(repo, "config", "user.email", "t@example.com")
    gitops.git(repo, "config", "user.name", "Test")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-m", "initial import")
    for subject in commits:
        (repo / "CHANGELOG").write_text(subject, encoding="utf-8")
        gitops.git(repo, "add", "-A")
        gitops.git(repo, "commit", "-m", subject)
    registry.add_plugin(home, Plugin(name=name, repo="git@example.com:acme/p.git", path=str(repo)))
    return repo


FULL = [
    ("README.md",
     "# payments-service\n\nSettles card payments and issues refunds for the widget"
     " platform.\n\nSecond paragraph nobody needs.\n"),
    ("pyproject.toml",
     '[project]\nname = "payments"\ndescription = "Card settlement and refunds"\n'),
    ("src/settle.py", "def settle():\n    return 1\n"),
    ("src/refund.py", "def refund():\n    return 1\n"),
    ("tests/test_settle.py", "def test():\n    pass\n"),
    ("infra/main.tf", "resource x {}\n"),
    ("AGENTS.md", "# agent notes\n"),
]


def describe(tmp_path, home, **kw):
    repo = app_repo(tmp_path, home, files=FULL, **kw)
    # Untracked, and a dependency tree at that: the repo's shape is what git tracks, not
    # what a walk would find. Counting `node_modules` as this repo's language mix — and
    # walking it at all — is unbounded work on somebody else's code.
    (repo / "node_modules" / "left-pad").mkdir(parents=True)
    (repo / "node_modules" / "left-pad" / "index.js").write_text("x\n", encoding="utf-8")
    for i in range(5):
        (repo / "node_modules" / f"vendor{i}.py").write_text("x\n", encoding="utf-8")
    return scaffold.describe(home, "payments-service")


def test_every_source_the_skill_claims_to_read_is_in_the_bundle(tmp_path):
    """The skill promises a draft from six sources. A missing one is a source it invents."""
    home = tmp_path / "home"
    bundle = describe(tmp_path, home, commits=["fix: retry settlement on 429", "docs: readme"])
    sources = bundle["sources"]

    assert sources["readme"].startswith("Settles card payments")
    assert "Second paragraph" not in sources["readme"], "the first paragraph, not the file"
    assert {"file": "pyproject.toml", "name": "payments",
            "description": "Card settlement and refunds"} in sources["manifests"]
    assert "src" in sources["tree"] and "infra" in sources["tree"]
    assert sources["languages"]["py"] == 3, "tracked files only — not five vendored ones too"
    assert sources["languages"]["tf"] == 1
    assert "js" not in sources["languages"]
    assert "node_modules" not in sources["tree"]
    assert "fix: retry settlement on 429" in sources["recent_subjects"]
    assert "AGENTS.md" in sources["agent_docs"]


def test_the_bundle_names_the_boundary_the_user_has_to_draw(tmp_path):
    """A new scope that does not say where it ENDS steals candidates from its siblings."""
    home = tmp_path / "home"
    kb = tmp_path / "team-kb"
    kb.mkdir()
    (kb / "MNEME.md").write_text(
        "# team-kb — knowledge scope\n\n## Scope statement\n\n"
        "Everything about the widget platform.\n",
        encoding="utf-8",
    )
    registry.add_plugin(home, Plugin(name="team-kb", repo="r", path=str(kb)))

    bundle = describe(tmp_path, home)

    siblings = {s["name"]: s["scope"] for s in bundle["siblings"]}
    assert "team-kb" in siblings
    assert "widget platform" in siblings["team-kb"]
    assert "payments-service" not in siblings, "a repo is not its own sibling"


def test_the_bundle_reports_the_mode_adoption_will_pick(tmp_path):
    home = tmp_path / "home"
    bundle = describe(tmp_path, home)
    assert bundle["repo"]["mode"] == "plain"
    # The reason must match the mode, not merely be a non-empty string ("x" passed).
    assert "not a knowledge plugin" in bundle["repo"]["why"]
    assert bundle["repo"]["knowledge_root"] == "mneme-index"


def test_repo_content_is_framed_as_data_on_both_sides(tmp_path):
    """A README is contributor-authored text quoted into an instruction context."""
    home = tmp_path / "home"
    bundle = describe(tmp_path, home)
    keys = list(bundle)
    assert keys[0] == "instructions", keys
    assert keys[-1] == "standing_rule", keys
    assert templates.UNTRUSTED_INPUT_RULE in bundle["instructions"]
    assert templates.UNTRUSTED_INPUT_RULE in bundle["standing_rule"]


def test_the_draft_must_describe_what_belongs_not_what_the_product_is(tmp_path):
    """A README is marketing. Marketing prose as a routing prompt matches everything."""
    home = tmp_path / "home"
    bundle = describe(tmp_path, home)
    assert "what knowledge belongs" in bundle["instructions"].lower()


def test_a_repo_with_none_of_it_still_describes_cleanly(tmp_path):
    """An empty repo yields empty sources, never a crash and never an invented scope."""
    home = tmp_path / "home"
    app_repo(tmp_path, home, name="bare", files=[(".keep", "")])
    bundle = scaffold.describe(home, "bare")
    assert bundle["sources"]["readme"] == ""
    assert bundle["sources"]["manifests"] == []
    assert bundle["repo"]["mode"] == "plain"


@pytest.mark.parametrize("manifest", ["package.json", "pyproject.toml", "Cargo.toml"])
def test_a_manifest_that_will_not_parse_is_a_missing_source_not_a_failure(tmp_path, manifest):
    """EVERY format, not just the one that happens to raise JSONDecodeError.

    Narrowing the `except` to JSON alone survived a mutation run: the only malformed
    manifest under test was JSON, so nothing covered the TOML path — where a broken
    `pyproject.toml` would have escaped as a raw TOMLDecodeError out of a command whose
    whole job is reading a repo mneme did not write.
    """
    home = tmp_path / "home"
    app_repo(tmp_path, home, name="broken", files=[(manifest, "{[not any of these")])
    bundle = scaffold.describe(home, "broken")
    assert bundle["sources"]["manifests"] == []


def test_a_source_is_read_up_to_its_bound_never_past_it(tmp_path):
    """Asserted on the reader itself: every caller also slices, which hides the bug.

    A 50 KB README still yielded 1000 characters with the bound removed, because
    `_first_paragraph` trims at the end — so the file was pulled into memory whole and the
    test said nothing about it. A contributor picks that size.
    """
    big = tmp_path / "big.md"
    big.write_text("x" * 200_000, encoding="utf-8")
    assert len(scaffold._text(big, 100)) == 100
    assert scaffold._text(tmp_path / "absent.md", 100) == ""


def test_unreadable_and_oversized_sources_are_bounded_not_fatal(tmp_path):
    """Everything here is contributor-chosen input, including its length."""
    home = tmp_path / "home"
    repo = app_repo(
        tmp_path, home, name="huge",
        files=[("README.md", "# huge\n\n" + "x" * 50_000 + "\n"),
               ("package.json", "{not json")],
    )
    (repo / "bad.md").write_bytes(b"\xff\xfe\x00broken")
    bundle = scaffold.describe(home, "huge")
    assert len(bundle["sources"]["readme"]) <= 1000
    assert bundle["sources"]["manifests"] == [], "a manifest that will not parse is not a source"


def test_describe_is_reachable_from_the_cli_as_json(tmp_path, capsys):
    home = tmp_path / "home"
    app_repo(tmp_path, home, files=FULL)
    code, out, _ = run(capsys, "--home", str(home), "adopt", "payments-service", "--describe")
    assert code == 0
    bundle = json.loads(out)
    assert bundle["repo"]["mode"] == "plain"
    # `--describe` reads and reports. It must not have adopted anything.
    assert not (tmp_path / "payments-service" / "MNEME.md").exists()


# --- every bound, not just the one that had a test ---------------------------
#
# `describe`'s output is contributor-authored repo content pasted into an agent's prompt.
# Only `_text`/README was covered, so `_TREE_ENTRIES`, `_SUBJECTS`, `_SUBJECT_CHARS` and
# `_SIBLING_SCOPE_CHARS` were all removable with the suite green — a context blow-up and a
# widened injection surface, both chosen by whoever wrote the repo.


def test_the_tree_and_language_lists_are_bounded(tmp_path):
    home = tmp_path / "home"
    files = [(f"dir{i:03d}/f.py", "x\n") for i in range(120)]
    files += [(f"ext{i}/f.e{i}", "x\n") for i in range(30)]
    app_repo(tmp_path, home, name="wide", files=files)
    sources = scaffold.describe(home, "wide")["sources"]
    assert len(sources["tree"]) <= scaffold._TREE_ENTRIES
    assert len(sources["languages"]) <= 12


def test_commit_subjects_are_bounded_in_count_and_length(tmp_path):
    home = tmp_path / "home"
    app_repo(
        tmp_path, home, name="chatty",
        files=[("a.txt", "x\n")],
        commits=[f"fix: {'y' * 400} number {i}" for i in range(25)],
    )
    subjects = scaffold.describe(home, "chatty")["sources"]["recent_subjects"]
    assert len(subjects) <= scaffold._SUBJECTS
    assert all(len(s) <= scaffold._SUBJECT_CHARS for s in subjects)


def test_a_sibling_scope_is_bounded_and_one_bad_sibling_breaks_nothing(tmp_path):
    """`_siblings` was the one reader that ignored the module's own bounded helper.

    `read_scope_statement` caught only `OSError`, so a single invalid UTF-8 byte in ANY
    registered repo's MNEME.md raised out of `describe` — one bad sibling bricked adoption
    of every other repo — and a huge one was read whole to produce 400 characters.
    """
    home = tmp_path / "home"

    huge = tmp_path / "huge-kb"
    huge.mkdir()
    (huge / "MNEME.md").write_text(
        "# huge\n\n## Scope statement\n\n" + "z" * 300_000 + "\n", encoding="utf-8"
    )
    registry.add_plugin(home, Plugin(name="huge-kb", repo="r", path=str(huge)))

    broken = tmp_path / "broken-kb"
    broken.mkdir()
    (broken / "MNEME.md").write_bytes(b"# broken\n\n## Scope statement\n\n\xff\xfe\x00bad\n")
    registry.add_plugin(home, Plugin(name="broken-kb", repo="r", path=str(broken)))

    gone = tmp_path / "gone-kb"
    registry.add_plugin(home, Plugin(name="gone-kb", repo="r", path=str(gone)))

    bundle = describe(tmp_path, home)

    scopes = {s["name"]: s["scope"] for s in bundle["siblings"]}
    assert set(scopes) == {"huge-kb", "broken-kb", "gone-kb"}
    assert len(scopes["huge-kb"]) <= scaffold._SIBLING_SCOPE_CHARS
    assert scopes["gone-kb"] == ""


def test_a_manifest_field_cannot_flood_the_bundle(tmp_path):
    home = tmp_path / "home"
    app_repo(
        tmp_path, home, name="loud",
        files=[("package.json",
                '{"name": "' + "n" * 150_000 + '", "description": "' + "d" * 40_000 + '"}')],
    )
    manifests = scaffold.describe(home, "loud")["sources"]["manifests"]
    assert manifests, "the manifest is still a source"
    assert all(len(m["name"]) <= scaffold._MANIFEST_FIELD_CHARS for m in manifests)
    assert all(len(m["description"]) <= scaffold._MANIFEST_FIELD_CHARS for m in manifests)


def test_a_symlinked_source_is_not_followed_out_of_the_repo(tmp_path):
    """`README.md -> /etc/passwd` is git-trackable and put that file into the bundle."""
    home = tmp_path / "home"
    secret = tmp_path / "secret.txt"
    secret.write_text("root:x:0:0:root:/root:/bin/bash\n", encoding="utf-8")
    repo = app_repo(tmp_path, home, name="linked", files=[("a.txt", "x\n")])
    (repo / "README.md").symlink_to(secret)

    assert scaffold.describe(home, "linked")["sources"]["readme"] == ""


def test_a_missing_clone_is_reported_not_guessed(tmp_path):
    home = tmp_path / "home"
    registry.add_plugin(home, Plugin(name="ghost", repo="r", path=str(tmp_path / "nope")))
    with pytest.raises(MnemeError, match="clone"):
        scaffold.describe(home, "ghost")


def test_an_unreadable_skills_dir_does_not_crash_the_command(tmp_path):
    """`iterdir()` on mode-000 raises, out of a command whose job is reading someone's repo."""
    import os

    home = tmp_path / "home"
    repo = app_repo(tmp_path, home, name="locked", files=[("skills/x/SKILL.md", "---\n---\n")])
    os.chmod(repo / "skills", 0o000)
    try:
        assert scaffold.describe(home, "locked")["repo"]["mode"] in ("plain", "plugin")
    finally:
        os.chmod(repo / "skills", 0o755)
