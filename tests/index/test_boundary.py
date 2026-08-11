import re
from pathlib import Path

INDEX_DIR = Path(__file__).resolve().parents[2] / "core" / "mneme_index"

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(mneme_core[\w.]*)\s+import\s+([^\n]+)|import\s+(mneme_core[\w.]*))",
    re.MULTILINE,
)

ALLOWED_MODULES = {"mneme_core.errors", "mneme_core.units"}
ALLOWED_NAMES = {"units", "errors"}


def test_mneme_index_imports_only_units_and_errors():
    assert INDEX_DIR.is_dir()
    for py in sorted(INDEX_DIR.glob("*.py")):
        source = py.read_text(encoding="utf-8")
        for m in _IMPORT_RE.finditer(source):
            from_module, names, plain_module = m.group(1), m.group(2), m.group(3)
            if plain_module is not None:
                assert plain_module in ALLOWED_MODULES, f"{py.name}: import {plain_module}"
            elif from_module == "mneme_core":
                imported = {n.strip().split(" ")[0] for n in names.split(",")}
                assert imported <= ALLOWED_NAMES, f"{py.name}: from mneme_core import {imported}"
            else:
                assert from_module in ALLOWED_MODULES, f"{py.name}: from {from_module} import …"
