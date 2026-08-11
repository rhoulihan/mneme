import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_package_imports_and_has_version():
    import mneme_core

    assert isinstance(mneme_core.__version__, str)
    assert mneme_core.__version__.count(".") == 2


def test_mneme_error_is_exception():
    from mneme_core.errors import MnemeError

    assert issubclass(MnemeError, Exception)


def test_launcher_is_executable_python():
    launcher = REPO_ROOT / "bin" / "mneme"
    assert launcher.exists()
    result = subprocess.run(
        [sys.executable, str(launcher)], capture_output=True, text=True
    )
    # Until Task 10 wires the CLI, the launcher must fail gracefully, not traceback.
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
