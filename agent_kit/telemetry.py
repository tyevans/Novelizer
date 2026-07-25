"""Machinery-telemetry vocabulary for the agent loop and scheduler.

These six event types (and their payload shapes) are what BaseAgent.run_once
and Scheduler emit. Exactly one of run_finished / run_failed / run_cancelled
follows every run_started: a consumer counting starts minus terminals must
be able to reach zero. The emitter itself is injected — see TelemetryEmitter.
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


class TelemetryEmitter(Protocol):
    """What the loop needs from a telemetry recorder. Injected post-
    construction (agent.telemetry defaults to None = silent)."""

    async def emit(self, event_type: str, aggregate_id: str, payload) -> None: ...

    def in_llm_call(self, run_id: str) -> bool: ...
