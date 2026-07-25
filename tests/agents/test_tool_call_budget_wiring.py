"""Every agent that can call tools carries the soft tool-call budget.

The budget only works if it is installed, and there are thirteen hand-rolled
builders that each pass their own `middleware=` list to `create_deep_agent` --
exactly the shape that produced the Curator's silent skills gap and the
AGENT_NAMES fleet-visibility gap before it. So this sweep derives from
AGENT_REGISTRY (see tests/agents/tooled_builders.py): a fourteenth tooled agent
is covered the day it is registered, not the day someone remembers.

An untooled agent needs no budget -- with no tools it cannot over-survey -- so
the bare branch of each builder is deliberately left alone.
"""
from __future__ import annotations

import deepagents
import pytest

from agent_kit import ToolCallBudgetMiddleware
from novelizer.canon_fs.backend import CanonBackend
from tests.agents.tooled_builders import TOOLED_BUILDERS


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


def _budget(captured) -> ToolCallBudgetMiddleware | None:
    for m in captured.get("middleware", []):
        if isinstance(m, ToolCallBudgetMiddleware):
            return m
    return None


@pytest.mark.parametrize("module_name,func_name", TOOLED_BUILDERS)
def test_tooled_builder_installs_the_budget(module_name, func_name, captured_kwargs):
    _build(module_name, func_name, backend=CanonBackend(read_store=None), tools=[])
    assert _budget(captured_kwargs) is not None, (
        f"{func_name} installs no ToolCallBudgetMiddleware; its runs can survey "
        "until GraphRecursionError discards everything they produced"
    )


@pytest.mark.parametrize("module_name,func_name", TOOLED_BUILDERS)
def test_the_budget_is_ordered_before_tool_exclusion(module_name, func_name, captured_kwargs):
    """The budget withdraws every tool at the hard stop, so it must not be
    undone by a later middleware re-deriving the tool list. Ordering it first
    means ExcludeToolsMiddleware filters whatever the budget left -- filtering
    an empty list is still empty."""
    from agent_kit import ExcludeToolsMiddleware

    _build(module_name, func_name, backend=CanonBackend(read_store=None), tools=[])
    middleware = captured_kwargs.get("middleware", [])
    kinds = [type(m) for m in middleware]
    if ExcludeToolsMiddleware in kinds:
        assert kinds.index(ToolCallBudgetMiddleware) < kinds.index(ExcludeToolsMiddleware)


def test_chat_runner_installs_the_budget(captured_kwargs):
    """Chat runners take a different signature (agent_name) so they sit outside
    the parametrized sweep, but a chat pass calls the same tools."""
    from novelizer.chat.runners import build_chat_runner

    build_chat_runner(_FakeSettings(), "author",
                      backend=CanonBackend(read_store=None), tools=[])
    assert _budget(captured_kwargs) is not None


def test_the_default_budget_sits_above_the_healthy_run_mean(captured_kwargs):
    """26.5 tool calls was the measured healthy-run mean; a budget at or below
    it would land runs that were going to succeed anyway."""
    from agent_kit import TOOL_CALL_HARD_MARGIN, TOOL_CALL_SOFT_BUDGET

    assert TOOL_CALL_SOFT_BUDGET > 26.5
    assert TOOL_CALL_HARD_MARGIN > 0
    module_name, func_name = TOOLED_BUILDERS[0]
    _build(module_name, func_name, backend=CanonBackend(read_store=None), tools=[])
    mw = _budget(captured_kwargs)
    assert (mw._soft, mw._hard) == (
        TOOL_CALL_SOFT_BUDGET, TOOL_CALL_SOFT_BUDGET + TOOL_CALL_HARD_MARGIN)


def test_the_hard_stop_lands_well_inside_the_recursion_backstop():
    """The budget is only a fix if it fires first. Each tool call costs at least
    two graph steps (the model call and the tool node), so the hard stop must be
    far enough under the recursion limit that the backstop stays a backstop."""
    from agent_kit import GRAPH_RECURSION_LIMIT, TOOL_CALL_HARD_MARGIN, TOOL_CALL_SOFT_BUDGET

    hard = TOOL_CALL_SOFT_BUDGET + TOOL_CALL_HARD_MARGIN
    assert hard * 2 < GRAPH_RECURSION_LIMIT
