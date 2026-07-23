"""Enforce the import-linter contracts declared in pyproject.toml as part of the
normal test run.

Without this, `[tool.importlinter]` contracts (substrate package boundary,
tui_kit independence) only rot-proof the codebase if someone remembers to run
`lint-imports` by hand or in a separate CI step. This wraps that invocation in
a pytest so `pytest` alone catches a boundary violation.

Runs the real `import-linter` CLI as a subprocess against the repo's own
pyproject.toml -- no network, no DB, just static import-graph analysis.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _lint_imports_cmd():
    exe = shutil.which("lint-imports")
    if exe:
        return [exe]
    return [sys.executable, "-m", "importlinter"]


@pytest.mark.skipif(
    not (REPO_ROOT / "pyproject.toml").exists(),
    reason="requires repo pyproject.toml with [tool.importlinter] contracts",
)
def test_import_linter_contracts_are_kept():
    result = subprocess.run(
        [*_lint_imports_cmd(), "--config", str(REPO_ROOT / "pyproject.toml")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "import-linter contracts broken:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
