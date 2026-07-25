"""Every tooled builder appends OUTPUT_CONVENTIONS_NOTE in its backend
branch and omits it in the bare branch.

The existing per-builder tests assert note inclusion by string composition
only; here we capture the system_prompt actually handed to
deepagents.create_deep_agent (imported at call time inside each builder,
so patching the deepagents module attribute intercepts all of them).
"""
from __future__ import annotations

import deepagents
import pytest

from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE
from novelizer.canon_fs.backend import CanonBackend
from tests.agents.tooled_builders import TOOLED_BUILDERS

# Derived from AGENT_REGISTRY -- see tests/agents/tooled_builders.py. The
# hand-maintained version of this list omitted the Curator and the Triage agent,
# both of which shipped tooled but without the output contract.
BUILDERS = TOOLED_BUILDERS


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    author_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    author_temperature = 0.8
    llm_max_tokens = None


class _FakeGraph:
    def with_config(self, config):
        return self


@pytest.fixture
def captured_prompt(monkeypatch):
    captured: dict = {}

    def fake_create_deep_agent(*args, **kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt")
        return _FakeGraph()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    return captured


def _build(module_name: str, func_name: str, **kwargs):
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, func_name)(_FakeSettings(), **kwargs)


@pytest.mark.parametrize("module_name,func_name", BUILDERS)
def test_backend_branch_appends_note(module_name, func_name, captured_prompt):
    _build(
        module_name, func_name,
        backend=CanonBackend(read_store=None), tools=[],
    )
    prompt = captured_prompt["system_prompt"]
    assert prompt is not None, "builder never passed system_prompt"
    assert OUTPUT_CONVENTIONS_NOTE in prompt
    # Appended, not injected mid-prompt: the note is a trailing section.
    assert prompt.endswith(OUTPUT_CONVENTIONS_NOTE)


@pytest.mark.parametrize("module_name,func_name", BUILDERS)
def test_bare_branch_omits_note(module_name, func_name, captured_prompt):
    _build(module_name, func_name)
    prompt = captured_prompt["system_prompt"]
    assert prompt is not None, "builder never passed system_prompt"
    assert OUTPUT_CONVENTIONS_NOTE not in prompt
