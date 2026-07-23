"""Guard the `research-domain` console-script entry point declared in
pyproject.toml's `[project.scripts]`.

A typo in the `research_domain.cli:main` target path (module or attribute)
would previously only surface after a real `uv tool install .` /
`pip install .` by an end user. These tests resolve the entry point the same
way packaging tools do -- import the module, then look up the attribute --
without requiring a real install.
"""
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _console_scripts():
    pyproject = REPO_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    return data["project"]["scripts"]


def test_research_domain_entry_point_is_declared():
    scripts = _console_scripts()
    assert "research-domain" in scripts
    assert scripts["research-domain"] == "research_domain.cli:main"


def test_research_domain_entry_point_target_resolves():
    """Resolve `module:attr` exactly like importlib.metadata's EntryPoint.load
    does, so a typo anywhere in the target string fails this test instead of
    only failing after a packaged install."""
    target = _console_scripts()["research-domain"]
    module_name, _, attr_path = target.partition(":")
    assert module_name and attr_path, f"malformed entry point target: {target!r}"

    import importlib

    module = importlib.import_module(module_name)
    obj = module
    for part in attr_path.split("."):
        obj = getattr(obj, part)
    assert callable(obj)


def test_research_domain_entry_point_resolves_via_installed_metadata():
    """Belt-and-suspenders: if the project is installed (editable or not),
    confirm importlib.metadata's own entry-point resolution succeeds too --
    this is the exact mechanism `uv tool install` / a real console script
    invocation relies on."""
    from importlib.metadata import entry_points

    eps = entry_points(group="console_scripts")
    matches = [ep for ep in eps if ep.name == "research-domain"]
    if not matches:
        pytest.skip("project not installed with an editable/wheel metadata entry point")
    loaded = matches[0].load()
    assert callable(loaded)
