from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage, ToolMessage

from agent_kit.run_context import current_agent_name, current_run_budget

logger = logging.getLogger(__name__)

# Asked to land, tools still available: the run may need one more read to make
# its answer coherent, and taking the tools away at the first warning would
# degrade output the model was about to finish honestly.
_NUDGE = (
    "BUDGET NOTICE: you have used {used} of your {soft} tool calls for this pass. "
    "Stop surveying now and emit your structured response using what you already "
    "have. An incomplete answer built from what you have read is the expected "
    "outcome here; continuing to gather until you are cut off produces nothing at "
    "all. If some field is unsupported by what you read, say so in that field "
    "rather than reading further to fill it."
)

# Tools withdrawn: emit or nothing.
_FORCE = (
    "BUDGET EXCEEDED: {used} tool calls used and your tools have now been "
    "withdrawn. Emit your structured response immediately from what you have "
    "already gathered. Mark anything you could not verify as unverified rather "
    "than omitting it silently."
)


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    if name is not None:
        return name
    return tool.get("name", "")


class ExcludeToolsMiddleware(AgentMiddleware[Any, Any, Any]):
    """Strip named tools from the model request before the model sees them.

    Placed after deepagents' tool-injecting middleware so it can remove
    built-ins like `write_todos`."""

    def __init__(self, *, excluded: frozenset[str]) -> None:
        self._excluded = excluded

    def wrap_model_call(self, request, handler):
        if self._excluded:
            filtered = [t for t in request.tools if _tool_name(t) not in self._excluded]
            request = request.override(tools=filtered)
        return handler(request)

    async def awrap_model_call(self, request, handler):
        if self._excluded:
            filtered = [t for t in request.tools if _tool_name(t) not in self._excluded]
            request = request.override(tools=filtered)
        return await handler(request)


class ToolCallBudgetMiddleware(AgentMiddleware[Any, Any, Any]):
    """A soft tool-call budget that lands the plane instead of crashing it.

    The failure this replaces: a run that keeps surveying eventually trips
    LangGraph's recursion limit, and GraphRecursionError is raised BEFORE
    `structured_response` exists -- so every LLM call, every token and every
    tool result the run produced is discarded and the caller gets nothing. It is
    all-or-nothing, and the measured cost was a quarter of all runs and well
    over half of all LLM calls. Raising the recursion limit (already done three
    times, 25 -> 50 -> 100 -> 200) cannot help: every extra step is another
    call, so a higher ceiling makes each failure more expensive without making
    it rarer.

    So the budget converts the failure into a degraded success. Past
    `soft_budget` tool calls the model is told to emit what it has; past
    `soft_budget + hard_margin` its tools are withdrawn so it cannot keep
    surveying. A partially-surveyed emission is worse than a complete one and
    infinitely better than nothing.

    Emission is forced by emptying `request.tools`, NOT by rewriting
    `response_format`. langchain appends the structured-output tools to the
    final tool list *after* middleware runs and sets `tool_choice="any"`
    whenever they exist (see langchain.agents.factory), so an empty
    `request.tools` leaves exactly one callable tool: the one that emits. That
    also keeps the mechanism correct under either output strategy -- with a
    native/provider strategy there is no tool to call and no tool to survey
    with, so the model can only answer.

    Implemented as middleware rather than a counter inside the agent loop
    because `wrap_model_call` is the only place that sees each model request
    *before* it is sent and can rewrite it. A counter in BaseAgent could observe
    the damage afterwards but not prevent it, and the graph is reused across
    runs, so per-run state cannot live on this instance either -- the count is
    re-derived from the conversation on every call.
    """

    def __init__(self, *, soft_budget: int, hard_margin: int) -> None:
        self._soft = soft_budget
        self._hard = soft_budget + hard_margin

    def _shape(self, request):
        """Return the request the model should actually see."""
        # Re-derived per call rather than accumulated on self: one graph serves
        # every run of its agent, so instance state would leak across runs.
        used = sum(1 for m in request.messages if isinstance(m, ToolMessage))
        if used >= self._hard:
            stage, template, tools = "forced", _FORCE, []
        elif used >= self._soft:
            stage, template, tools = "nudged", _NUDGE, request.tools
        else:
            self._note(used, "")
            return request
        self._note(used, stage)
        # Appended at the END of the conversation, not folded into the system
        # prompt: recency is what makes a stop instruction win against thirty
        # turns of gathering momentum.
        notice = SystemMessage(content=template.format(used=used, soft=self._soft))
        return request.override(messages=[*request.messages, notice], tools=tools)

    def _note(self, used: int, stage: str) -> None:
        budget = current_run_budget.get()
        if budget is None:
            # No holder installed (a bare graph, a non-BaseAgent call site). The
            # budget still applies; only the telemetry marker is lost.
            if stage:
                logger.warning("tool-call budget %s at %d calls", stage, used)
            return
        if budget.record(used, stage):
            logger.warning(
                "%s: tool-call budget %s at %d tool calls (soft=%d, hard=%d) -- "
                "output for this run is degraded",
                current_agent_name.get() or "agent", stage, used, self._soft, self._hard,
            )

    def wrap_model_call(self, request, handler):
        return handler(self._shape(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._shape(request))
