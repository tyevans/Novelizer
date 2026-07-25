"""agent_kit: domain-neutral agent execution machinery.

The third extraction (after substrate/ and tui_kit/): the BaseAgent
poll/work/commit loop chassis, the readiness-sorted Scheduler, LLM runner
construction, and the machinery-telemetry vocabulary — extracted from
novelizer's shape, consumed first by research_domain.

Import from this top level only; submodule imports are forbidden by an
import-linter contract (see pyproject.toml).
"""
from agent_kit.base import (
    BaseAgent,
    Runner,
    # Re-exported at the package root (not in __all__ -- it stays a kit
    # internal) so a domain consumer feeding the pool's AIMD from its own drain
    # loop can reach it without importing agent_kit.base directly, which the
    # package-boundary contract forbids.
    _is_rate_limit_error,
)
from agent_kit.llm import (
    CONTEXT_WINDOW_TOKENS,
    GRAPH_RECURSION_LIMIT,
    LIGHT_MAX_TOKENS,
    LLM_MAX_RETRIES,
    THINKING_TEMPLATE_KEY,
    TOOL_CALL_HARD_MARGIN,
    TOOL_CALL_SOFT_BUDGET,
    build_agent_runner,
    build_chat_model,
    build_light_model,
    build_simple_runner,
)
from agent_kit.middleware import ExcludeToolsMiddleware, ToolCallBudgetMiddleware
from agent_kit.pool import AdaptivePool
from agent_kit.run_context import current_agent_name, current_run_id
from agent_kit.scheduler import Scheduler
from agent_kit.telemetry import (
    AgentRunCancelled,
    AgentRunFailed,
    AgentRunTruncated,
    AgentRunFinished,
    AgentRunStarted,
    SchedulerEligibilityChanged,
    SchedulerPicked,
    TelemetryEmitter,
    TelemetryEventType,
)

__all__ = [
    "AdaptivePool",
    "BaseAgent",
    "Runner",
    "Scheduler",
    "TelemetryEventType",
    "TelemetryEmitter",
    "AgentRunStarted",
    "AgentRunFinished",
    "AgentRunFailed",
    "AgentRunCancelled",
    "AgentRunTruncated",
    "SchedulerPicked",
    "SchedulerEligibilityChanged",
    "current_run_id",
    "current_agent_name",
    "build_chat_model",
    "build_agent_runner",
    # The light path: a cheap-call model builder and the graph-free runner that
    # pairs with it. Exported at the root for the same package-boundary reason
    # as everything else here.
    "build_light_model",
    "build_simple_runner",
    "LIGHT_MAX_TOKENS",
    "THINKING_TEMPLATE_KEY",
    "GRAPH_RECURSION_LIMIT",
    "CONTEXT_WINDOW_TOKENS",
    "LLM_MAX_RETRIES",
    "ExcludeToolsMiddleware",
    "ToolCallBudgetMiddleware",
    "TOOL_CALL_SOFT_BUDGET",
    "TOOL_CALL_HARD_MARGIN",
]
