"""The soft tool-call budget: land the plane instead of crashing it.

Measured over a real 3h22m window, 22 of 81 terminal runs (27%) died on
GraphRecursionError, burning 1480 LLM calls (58% of all attributed calls) and
198993 output tokens that were ALL discarded -- the graph raises before
`structured_response` exists, so the run returns nothing at all. Doomed runs
averaged 67.3 LLM calls against 17.6 for healthy ones, and the breadth was
genuine (71.8 distinct tool calls of 80.8) rather than a degenerate loop.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent_kit import ToolCallBudgetMiddleware
from agent_kit.run_context import RunBudget, current_run_budget


class FakeTool:
    def __init__(self, name):
        self.name = name


class FakeRequest:
    """Mirrors the ModelRequest surface the middleware touches."""

    def __init__(self, messages, tools=None):
        self.messages = messages
        self.tools = tools if tools is not None else [FakeTool("search_canon")]

    def override(self, **overrides):
        return FakeRequest(
            overrides.get("messages", self.messages),
            overrides.get("tools", self.tools),
        )


def _history(tool_calls: int):
    """A conversation carrying `tool_calls` completed tool calls."""
    messages = [HumanMessage(content="go")]
    for i in range(tool_calls):
        messages.append(AIMessage(content="", tool_calls=[
            {"name": "search_canon", "args": {}, "id": f"c{i}"}]))
        messages.append(ToolMessage(content="result", tool_call_id=f"c{i}"))
    return messages


@pytest.fixture
def budget():
    """run_once installs the holder; these tests stand in for it."""
    record = RunBudget()
    token = current_run_budget.set(record)
    yield record
    current_run_budget.reset(token)


def _run(mw, request):
    seen = {}

    def handler(req):
        seen["request"] = req
        return "response"

    assert mw.wrap_model_call(request, handler) == "response"
    return seen["request"]


def test_under_the_soft_budget_the_request_is_untouched(budget):
    mw = ToolCallBudgetMiddleware(soft_budget=30, hard_margin=10)
    request = FakeRequest(_history(29))
    passed = _run(mw, request)
    assert passed is request
    assert budget.stage == ""
    assert budget.tool_calls == 29


def test_at_the_soft_budget_a_system_nudge_is_appended_and_tools_survive(budget):
    """The nudge must not remove the surveying tools: the model is asked to
    land, not prevented from a last necessary read. Appended at the END of the
    conversation rather than folded into the system prompt -- recency is what
    makes a stop instruction win over 30 turns of momentum."""
    mw = ToolCallBudgetMiddleware(soft_budget=30, hard_margin=10)
    request = FakeRequest(_history(30))
    passed = _run(mw, request)
    assert passed is not request
    last = passed.messages[-1]
    assert isinstance(last, SystemMessage)
    assert "structured response" in last.text.lower()
    assert [t.name for t in passed.tools] == ["search_canon"]
    assert budget.stage == "nudged"


def test_at_the_hard_budget_every_tool_is_stripped_so_it_must_emit(budget):
    """Emission is forced by taking the surveying tools away, not by rewriting
    response_format: langchain adds the structured-output tools AFTER
    middleware and sets tool_choice="any" when they exist, so an empty
    request.tools leaves exactly one callable tool -- the one that emits."""
    mw = ToolCallBudgetMiddleware(soft_budget=30, hard_margin=10)
    request = FakeRequest(_history(40))
    passed = _run(mw, request)
    assert passed.tools == []
    assert isinstance(passed.messages[-1], SystemMessage)
    assert budget.stage == "forced"


def test_the_stage_only_escalates_never_downgrades(budget):
    """One run makes many model calls. Once forced, a later call that somehow
    counts fewer tool messages (summarization trims history) must not reset the
    record to 'nudged' and report the run as less degraded than it was."""
    mw = ToolCallBudgetMiddleware(soft_budget=30, hard_margin=10)
    _run(mw, FakeRequest(_history(40)))
    assert budget.stage == "forced"
    _run(mw, FakeRequest(_history(30)))
    assert budget.stage == "forced"


def test_budget_is_configurable(budget):
    mw = ToolCallBudgetMiddleware(soft_budget=3, hard_margin=1)
    assert not isinstance(_run(mw, FakeRequest(_history(2))).messages[-1], SystemMessage)
    assert isinstance(_run(mw, FakeRequest(_history(3))).messages[-1], SystemMessage)
    assert _run(mw, FakeRequest(_history(4))).tools == []


def test_no_holder_installed_still_shapes_the_request(budget_free=None):
    """The middleware must work standalone: a consumer that never installs a
    RunBudget (a bare graph in a test, a non-BaseAgent call site) still gets
    the budget enforced, it just loses the telemetry marker."""
    assert current_run_budget.get() is None
    mw = ToolCallBudgetMiddleware(soft_budget=2, hard_margin=1)
    assert _run(mw, FakeRequest(_history(3))).tools == []


async def test_async_path_shapes_the_request_the_same_way(budget):
    mw = ToolCallBudgetMiddleware(soft_budget=30, hard_margin=10)
    seen = {}

    async def handler(req):
        seen["request"] = req
        return "response"

    assert await mw.awrap_model_call(FakeRequest(_history(40)), handler) == "response"
    assert seen["request"].tools == []
    assert budget.stage == "forced"
