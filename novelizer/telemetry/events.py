from __future__ import annotations
from pydantic import BaseModel

from agent_kit import (
    AgentRunFailed,
    AgentRunFinished,
    AgentRunStarted,
    SchedulerEligibilityChanged,
    SchedulerPicked,
    TelemetryEventType as _MachineryEventType,
)

class TelemetryEventType(_MachineryEventType):
    """Machinery event vocabulary. Persisted to telemetry.db (a separate
    EventStore), never to the domain log. The five loop/scheduler constants
    come from agent_kit (same strings, shared with every kit consumer); the
    LLM/tool-call vocabulary below is recorder-side and stays here until the
    recorder extraction campaign."""

    LLM_CALL_STARTED = "llm.call_started"
    LLM_CALL_FINISHED = "llm.call_finished"
    LLM_CALL_FAILED = "llm.call_failed"
    TOOL_CALL_STARTED = "tool.call_started"
    TOOL_CALL_FINISHED = "tool.call_finished"
    TOOL_CALL_FAILED = "tool.call_failed"


class LlmCallStarted(BaseModel):
    """Carries the full rendered prompt — this is what powers prompt
    inspection in both the live view and the trace."""

    run_id: str
    agent_name: str
    call_index: int
    model: str
    prompt: str
    delegate: str = ""


class LlmCallFinished(BaseModel):
    run_id: str
    agent_name: str
    call_index: int
    model: str
    duration_s: float
    output_tokens: int
    delegate: str = ""


class LlmCallFailed(BaseModel):
    run_id: str
    agent_name: str
    call_index: int
    model: str
    duration_s: float
    error_type: str
    error_message: str
    delegate: str = ""


class ToolCallStarted(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    input_summary: str  # str(tool input), truncated to 300 chars
    delegate: str = ""


class ToolCallFinished(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    output_chars: int
    input_summary: str = ""
    output_summary: str = ""
    delegate: str = ""


class ToolCallFailed(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    error_type: str
    error_message: str
    input_summary: str = ""
    delegate: str = ""


class TokenDelta(BaseModel):
    """One streamed chunk of model output. Bus-only: NEVER persisted (the
    finished chapter already lands in the domain log)."""

    run_id: str
    agent_name: str
    text: str
    kind: str = "text"  # "text" (answer content) | "thinking" (reasoning_content)


class ToolSummaryReady(BaseModel):
    """A cheap-LLM one-line summary of a finished/failed tool call, matched
    back to its block by (run_id, tool_name, input_summary) rather than a
    synthetic id -- the same event is folded independently into more than
    one LiveRunState (the merged "All" view and each per-agent view), and
    those don't share block numbering. Bus-only: NEVER persisted."""

    run_id: str
    agent_name: str
    tool_name: str
    input_summary: str
    summary: str
