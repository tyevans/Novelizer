"""agent_kit: domain-neutral agent execution machinery.

The third extraction (after substrate/ and tui_kit/): the BaseAgent
poll/work/commit loop chassis, the readiness-sorted Scheduler, LLM runner
construction, and the machinery-telemetry vocabulary — extracted from
novelizer's shape, consumed first by research_domain.

Import from this top level only; submodule imports are forbidden by an
import-linter contract (see pyproject.toml).
"""
from agent_kit.base import (
    PASS_BACKOFF_MULTIPLIER,
    RATE_LIMIT_BACKOFF_MULTIPLIER,
    BaseAgent,
    Runner,
)
from agent_kit.llm import (
    CONTEXT_WINDOW_TOKENS,
    GRAPH_RECURSION_LIMIT,
    LLM_MAX_RETRIES,
    build_agent_runner,
    build_chat_model,
)
from agent_kit.middleware import ExcludeToolsMiddleware
from agent_kit.run_context import current_agent_name, current_run_id
from agent_kit.scheduler import Scheduler
from agent_kit.telemetry import (
    AgentRunFailed,
    AgentRunFinished,
    AgentRunStarted,
    SchedulerEligibilityChanged,
    SchedulerPicked,
    TelemetryEmitter,
    TelemetryEventType,
)

__all__ = [
    "BaseAgent",
    "Runner",
    "PASS_BACKOFF_MULTIPLIER",
    "RATE_LIMIT_BACKOFF_MULTIPLIER",
    "Scheduler",
    "TelemetryEventType",
    "TelemetryEmitter",
    "AgentRunStarted",
    "AgentRunFinished",
    "AgentRunFailed",
    "SchedulerPicked",
    "SchedulerEligibilityChanged",
    "current_run_id",
    "current_agent_name",
    "build_chat_model",
    "build_agent_runner",
    "GRAPH_RECURSION_LIMIT",
    "CONTEXT_WINDOW_TOKENS",
    "LLM_MAX_RETRIES",
    "ExcludeToolsMiddleware",
]
