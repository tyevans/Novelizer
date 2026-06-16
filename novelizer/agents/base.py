from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional
import ollama
from pydantic_graph import BaseNode, End, Graph, GraphRunContext
from novelizer.store.queries import Store


@dataclass
class AgentState:
    agent_name: str
    paused: bool = False
    context: dict[str, Any] = field(default_factory=dict)


class Idle(BaseNode[AgentState]):
    async def run(self, ctx: GraphRunContext[AgentState]) -> "Polling":
        return Polling()


class Polling(BaseNode[AgentState]):
    async def run(self, ctx: GraphRunContext[AgentState]) -> "Working":
        return Working()


class Working(BaseNode[AgentState]):
    async def run(self, ctx: GraphRunContext[AgentState]) -> "Committing":
        return Committing()


class Committing(BaseNode[AgentState]):
    async def run(self, ctx: GraphRunContext[AgentState]) -> Idle:
        return Idle()


_base_graph = Graph(nodes=[Idle, Polling, Working, Committing])


class BaseAgent:
    """
    Wraps a pydantic-graph instance with pause/resume, rate limiting,
    and a readiness_check hook for the scheduler.

    Subclasses override poll(), work(), and commit() instead of touching
    graph nodes directly. The graph is re-entered on each scheduler tick.
    """

    def __init__(self, name: str, store: Store, min_interval: int, llm_model: str = "llama3.2") -> None:
        self.name = name
        self.store = store
        self.min_interval = min_interval
        self.llm_model = llm_model
        self.paused = False
        self._last_run: float = 0.0

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def ready_for_interval(self) -> bool:
        return (time.monotonic() - self._last_run) >= self.min_interval

    async def readiness_check(self) -> float:
        """Return 0.0–1.0 indicating how much work is available. Override in subclasses."""
        return 0.0

    async def poll(self, state: AgentState) -> None:
        """Fetch context from store into state.context. Override in subclasses."""

    async def work(self, state: AgentState) -> None:
        """Run LLM call(s) using state.context. Store results in state.context. Override."""

    async def commit(self, state: AgentState) -> None:
        """Write results from state.context to store. Override in subclasses."""

    def _llm(self, messages: list[dict]) -> str:
        resp = ollama.chat(model=self.llm_model, messages=messages)
        return resp["message"]["content"]

    async def run_once(self) -> None:
        """Execute one full poll→work→commit cycle."""
        if self.paused:
            return
        state = AgentState(agent_name=self.name)
        await self.poll(state)
        await self.work(state)
        await self.commit(state)
        self._last_run = time.monotonic()
