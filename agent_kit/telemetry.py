"""Machinery-telemetry vocabulary for the agent loop and scheduler.

These event types (and their payload shapes) are what BaseAgent.run_once and
Scheduler emit. Exactly one of run_finished / run_failed / run_cancelled
follows every run_started: a consumer counting starts minus terminals must
be able to reach zero. run_truncated is the one non-terminal member -- it
annotates a run that still finishes. The emitter itself is injected — see TelemetryEmitter.
Payload field names match novelizer's telemetry vocabulary so existing
recorders and tui_kit adapters understand them unchanged.
"""
from __future__ import annotations
from typing import Protocol

from pydantic import BaseModel


class TelemetryEventType:
    SCHEDULER_PICKED = "scheduler.picked"
    SCHEDULER_ELIGIBILITY_CHANGED = "scheduler.eligibility_changed"
    AGENT_RUN_STARTED = "agent.run_started"
    AGENT_RUN_FINISHED = "agent.run_finished"
    AGENT_RUN_FAILED = "agent.run_failed"
    AGENT_RUN_CANCELLED = "agent.run_cancelled"
    AGENT_RUN_TRUNCATED = "agent.run_truncated"


class SchedulerPicked(BaseModel):
    agent_name: str


class SchedulerEligibilityChanged(BaseModel):
    """Emitted on change of an agent's (eligible, reason) pair — never per tick."""

    agent_name: str
    eligible: bool
    reason: str  # "paused" | "running" | "backing off" | "readiness 0" | "ready"


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


class AgentRunCancelled(BaseModel):
    """A run cut off from outside (shutdown, a reclaimed dispatch slot) rather
    than one that failed. Distinct from AgentRunFailed because it is evidence
    of nothing about the agent: it carries no error, and a status surface must
    not read it as a fault."""

    run_id: str
    agent_name: str
    phase: str  # same contract as AgentRunFailed.phase
    duration_s: float


class AgentRunTruncated(BaseModel):
    """The tool-call budget had to intervene, so this run answered from less
    than it wanted to read.

    NOT a terminal event -- a truncated run still finishes, and still emits
    run_finished after this. It exists because a degraded output that nobody can
    distinguish from a complete one is its own trap: without this, a run landed
    by the budget and a run that surveyed everything it needed look identical
    downstream.
    """

    run_id: str
    agent_name: str
    stage: str  # "nudged" (asked to land) | "forced" (tools withdrawn)
    tool_calls: int


class TelemetryEmitter(Protocol):
    """What the loop needs from a telemetry recorder. Injected post-
    construction (agent.telemetry defaults to None = silent)."""

    async def emit(self, event_type: str, aggregate_id: str, payload) -> None: ...

    def in_llm_call(self, run_id: str) -> bool: ...
