"""Ambient identity and budget of the agent run currently executing.

Deliberately dependency-free so any layer (storage, telemetry, tools) can
read these without importing the rest of the kit.
"""
from __future__ import annotations
from contextvars import ContextVar
from dataclasses import dataclass

current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
current_agent_name: ContextVar[str] = ContextVar("current_agent_name", default="")


@dataclass
class RunBudget:
    """How close the current run came to its tool-call budget, and whether the
    budget had to intervene.

    MUTABLE ON PURPOSE, and that is the load-bearing detail: the middleware
    that fills this in runs inside the graph, several LangGraph nodes deep, and
    those nodes execute in child tasks. A child task gets a COPY of the context,
    so a `ContextVar.set()` down there is invisible to run_once up here. What
    the copy shares with the parent is the object itself -- so run_once installs
    one of these before invoking, the middleware mutates it in place, and
    run_once can still read the result afterwards.

    `stage` escalates and never downgrades: "" (untouched) -> "nudged" (asked to
    land) -> "forced" (tools withdrawn so it had to emit).
    """

    tool_calls: int = 0
    stage: str = ""

    STAGES = ("", "nudged", "forced")

    @property
    def truncated(self) -> bool:
        return self.stage != ""

    def record(self, tool_calls: int, stage: str) -> bool:
        """Note a budget observation. Returns True the first time each stage is
        reached, so a caller can log or emit once per run rather than once per
        model call -- a doomed run makes dozens."""
        self.tool_calls = max(self.tool_calls, tool_calls)
        if self.STAGES.index(stage) <= self.STAGES.index(self.stage):
            return False
        self.stage = stage
        return True


current_run_budget: ContextVar[RunBudget | None] = ContextVar(
    "current_run_budget", default=None)
