from __future__ import annotations
from pydantic import BaseModel


class TelemetryEventType:
    """Machinery event vocabulary. Persisted to telemetry.db (a separate
    EventStore), never to the domain log."""

    SCHEDULER_PICKED = "scheduler.picked"
    SCHEDULER_ELIGIBILITY_CHANGED = "scheduler.eligibility_changed"
    AGENT_RUN_STARTED = "agent.run_started"
    AGENT_RUN_FINISHED = "agent.run_finished"
    AGENT_RUN_FAILED = "agent.run_failed"
    LLM_CALL_STARTED = "llm.call_started"
    LLM_CALL_FINISHED = "llm.call_finished"
    LLM_CALL_FAILED = "llm.call_failed"
    TOOL_CALL_STARTED = "tool.call_started"
    TOOL_CALL_FINISHED = "tool.call_finished"
    TOOL_CALL_FAILED = "tool.call_failed"


class SchedulerPicked(BaseModel):
    agent_name: str


class SchedulerEligibilityChanged(BaseModel):
    """Emitted on change of an agent's (eligible, reason) pair — never per tick."""

    agent_name: str
    eligible: bool
    reason: str  # "paused" | "interval not elapsed" | "readiness 0" | "ready"


class AgentRunStarted(BaseModel):
    run_id: str
    agent_name: str


class AgentRunFinished(BaseModel):
    run_id: str
    agent_name: str
    duration_s: float


class AgentRunFailed(BaseModel):
    run_id: str
    agent_name: str
    error_type: str
    error_message: str
    phase: str  # "llm_call" if the crash happened inside an open LLM call, else "agent"
    duration_s: float


class LlmCallStarted(BaseModel):
    """Carries the full rendered prompt — this is what powers prompt
    inspection in both the live view and the trace."""

    run_id: str
    agent_name: str
    call_index: int
    model: str
    prompt: str


class LlmCallFinished(BaseModel):
    run_id: str
    agent_name: str
    call_index: int
    model: str
    duration_s: float
    output_tokens: int


class LlmCallFailed(BaseModel):
    run_id: str
    agent_name: str
    call_index: int
    model: str
    duration_s: float
    error_type: str
    error_message: str


class ToolCallStarted(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    input_summary: str  # str(tool input), truncated to 300 chars


class ToolCallFinished(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    output_chars: int


class ToolCallFailed(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    error_type: str
    error_message: str


class TokenDelta(BaseModel):
    """One streamed chunk of model output. Bus-only: NEVER persisted (the
    finished chapter already lands in the domain log)."""

    run_id: str
    agent_name: str
    text: str
