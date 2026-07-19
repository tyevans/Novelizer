from __future__ import annotations
import logging
from typing import Protocol
from pydantic import BaseModel, Field
from novelizer.canon.events import EventType, AgentRemark
from novelizer.agents.schemas import ThreadIntent, KnowledgeIntent, CausalIntent, ThemeIntent
from novelizer.agents import intents as intent_helpers

logger = logging.getLogger(__name__)


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)
    theme_intents: list[ThemeIntent] = Field(default_factory=list)


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

    async def _commit_thread_intents(
        self,
        intents: list[ThreadIntent],
        active_thread_ids: set[str],
        chapter_id: str = "",
        source: str = "declared",
    ) -> None:
        await intent_helpers.commit_thread_intents(
            self._committer, self.name, intents, active_thread_ids, chapter_id=chapter_id, source=source
        )

    async def _commit_theme_intents(
        self,
        intents: list[ThemeIntent],
        active_theme_ids: set[str],
        chapter_id: str = "",
        source: str = "declared",
    ) -> None:
        await intent_helpers.commit_theme_intents(
            self._committer, self.name, intents, active_theme_ids, chapter_id=chapter_id, source=source
        )

    async def _commit_knowledge_intents(
        self,
        intents: list[KnowledgeIntent],
        active_secret_ids: set[str],
        chapter_id: str = "",
        allowed_actions: frozenset[str] = frozenset({"plant", "learn", "reveal", "uses"}),
        source: str = "declared",
    ) -> None:
        await intent_helpers.commit_knowledge_intents(
            self._committer, self.name, intents, active_secret_ids, chapter_id=chapter_id,
            allowed_actions=allowed_actions, source=source,
        )

    async def _commit_causal_intents(
        self, intents: list[CausalIntent], valid_chapter_ids: set[str], source: str = "declared"
    ) -> None:
        await intent_helpers.commit_causal_intents(
            self._committer, self.name, intents, valid_chapter_ids, source=source
        )
