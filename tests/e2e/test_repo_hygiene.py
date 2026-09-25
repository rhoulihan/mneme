from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_contributing_covers_process():
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for token in ("python3 -m pytest", "docs/superpowers/plans", "bin/mneme lint"):
        assert token in text, token


def test_security_covers_reporting_and_scope():
    text = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "security advisor" in text.lower()
    for token in ("secret", "read-only", "hook"):
        assert token in text.lower(), token


def test_every_hook_script_is_executable_in_the_index():
    """A hook without the executable bit is `Permission denied` for every user, every turn.

    This shipped in v0.10.0: `user-prompt-submit.sh` went out at 100644 and the
    UserPromptSubmit hook failed on every prompt. The cause is a documented machine gotcha
    — this repo lives on WSL drvfs (`/mnt/c`), where `chmod +x` does not reach git's index;
    only `git update-index --chmod=+x` does. Two sibling scripts added in the same change
    were set correctly by hand and this one was missed, which is precisely why the rule is
    asserted here instead of remembered.

    Checked against the INDEX, not the working tree: the working tree mode on drvfs is
    meaningless, and the index is what ships.
    """
    import json
    import subprocess

    hooks = json.loads(
        (REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )["hooks"]
    referenced = {
        handler["command"].rsplit("/", 1)[-1]
        for groups in hooks.values()
        for group in groups
        for handler in group["hooks"]
        if handler.get("type") == "command"
    }
    assert referenced, "hooks.json references no command scripts"

    listing = subprocess.run(
        ["git", "ls-files", "-s", "hooks/scripts", "bin"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    modes = {}
    for line in listing:
        meta, path = line.split("\t", 1)
        modes[path.rsplit("/", 1)[-1]] = meta.split()[0]

    missing = sorted(referenced - set(modes))
    assert not missing, f"hooks.json names scripts that are not tracked: {missing}"

    not_executable = sorted(
        name for name, mode in modes.items() if mode != "100755"
    )
    assert not not_executable, (
        "these ship without the executable bit — fix with"
        f" `git update-index --chmod=+x <path>`: {not_executable}"
    )
