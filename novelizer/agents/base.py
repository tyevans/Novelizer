from __future__ import annotations
from typing import Protocol
from pydantic import BaseModel, Field
from novelizer.canon.events import EventType, AgentRemark


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)
    feed_note: str = ""


class Runner(Protocol):
    async def ainvoke(self, inputs: dict) -> dict: ...


class BaseAgent:
    name: str = "agent"

    def __init__(
        self,
        runner,
        read_store,
        committer,
        interval: int,
        name: str | None = None,
        personality: str = "",
    ) -> None:
        self._runner = runner
        self._read = read_store
        self._committer = committer
        self.interval = interval
        if name is not None:
            self.name = name
        self.personality = personality
        self.paused = False
        self._last_run = 0.0

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def ready_for_interval(self, now: float) -> bool:
        return (now - self._last_run) >= self.interval

    def mark_ran(self, now: float) -> None:
        self._last_run = now

    async def readiness(self) -> float:
        return 0.0

    async def run_once(self) -> None:
        pass

    async def _consume_signals(self, signals) -> None:
        for sig in signals:
            consumed = sig.model_copy(update={"consumed": True})
            await self._committer.commit(self.name, EventType.DIRECTOR_SIGNAL_CONSUMED, sig.id, consumed)

    async def _remark(self, note: str) -> None:
        """Emit a short in-personality feed line as agent.remarked. No-op if note is empty."""
        if not note:
            return
        await self._committer.commit(
            self.name, EventType.AGENT_REMARKED, self.name, AgentRemark(agent_name=self.name, note=note)
        )
