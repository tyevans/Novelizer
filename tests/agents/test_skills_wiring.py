"""Every tooled builder registers the craft skill packs in its backend
branch, and none registers them in its bare branch.

Mounting `/skills/` on the composite backend (Runtime._phase_a_toolkit) only
makes the packs *readable*; it is passing `skills=` to `create_deep_agent`
that installs deepagents' `SkillsMiddleware`, which is what lists each pack's
name + description in the system prompt. Without it an agent has the packs on
disk and no way to learn they exist -- `skills_route.CRAFT_SKILLS`' docstring
asserts "every tooled agent now sees the name and description of all packs",
and this test is what makes that assertion true rather than aspirational.

Same capture technique as test_output_conventions_note.py: `create_deep_agent`
is imported at call time inside each builder, so patching the module attribute
intercepts all of them.
"""
from __future__ import annotations

import deepagents
import pytest

from novelizer.canon_fs.backend import CanonBackend
from novelizer.canon_fs.skills_route import CRAFT_SKILLS
from tests.agents.tooled_builders import TOOLED_BUILDERS

# Every builder that accepts a `backend` kwarg -- i.e. every agent that can
# reach `/skills/` at all. The bare (push-mode) branch of each has no backend,
# so no skills route, so registering skills there would point at nothing.
# Derived from AGENT_REGISTRY: a hand-maintained copy of this list is how the
# Curator shipped unswept. See tests/agents/tooled_builders.py.
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
def captured_kwargs(monkeypatch):
    captured: dict = {}

    def fake_create_deep_agent(*args, **kwargs):
        captured.update(kwargs)
        return _FakeGraph()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    return captured


def _build(module_name: str, func_name: str, **kwargs):
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, func_name)(_FakeSettings(), **kwargs)


@pytest.mark.parametrize("module_name,func_name", BUILDERS)
def test_backend_branch_registers_craft_skills(module_name, func_name, captured_kwargs):
    _build(
        module_name, func_name,
        backend=CanonBackend(read_store=None), tools=[],
    )
    assert captured_kwargs.get("skills") == CRAFT_SKILLS, (
        f"{func_name} does not register the craft skill packs; its agent can read "
        "/skills/ but has no way to discover what is there"
    )


@pytest.mark.parametrize("module_name,func_name", BUILDERS)
def test_bare_branch_registers_no_skills(module_name, func_name, captured_kwargs):
    _build(module_name, func_name)
    assert not captured_kwargs.get("skills"), (
        f"{func_name} registers skills with no backend mounted -- the /skills/ "
        "route only exists on the composite backend"
    )


def test_chat_runner_backend_branch_registers_craft_skills(captured_kwargs):
    """Chat runners take a different signature (agent_name), so they sit
    outside the parametrized sweep -- but they are tooled the same way."""
    from novelizer.chat.runners import build_chat_runner

    build_chat_runner(
        _FakeSettings(), "author",
        backend=CanonBackend(read_store=None), tools=[],
    )
    assert captured_kwargs.get("skills") == CRAFT_SKILLS


def test_chat_runner_bare_branch_registers_no_skills(captured_kwargs):
    from novelizer.chat.runners import build_chat_runner

    build_chat_runner(_FakeSettings(), "author")
    assert not captured_kwargs.get("skills")
