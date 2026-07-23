# tests/substrate/test_import_boundary.py
import subprocess


def test_novelizer_and_research_domain_only_import_substrate_top_level():
    result = subprocess.run(
        ["lint-imports"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
