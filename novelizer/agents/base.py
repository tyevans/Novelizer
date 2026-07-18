from __future__ import annotations
import logging
from typing import Protocol
from pydantic import BaseModel, Field
from novelizer.canon.events import (
    EventType, AgentRemark, ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned,
)
from novelizer.canon.threads import slugify_thread_name
from novelizer.agents.schemas import ThreadIntent

logger = logging.getLogger(__name__)


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)


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
        self, intents: list[ThreadIntent], active_thread_ids: set[str], chapter_id: str = ""
    ) -> None:
        """Turn agent-declared ThreadIntent entries into thread.* commits.

        `plant` mints a new id via slugify_thread_name(intent.name) and is
        dropped only if the name is blank. `touch`/`pay_off`/`abandon` must
        cite an id present in `active_thread_ids` — the thread's known,
        non-terminal ids at poll time (see Author.poll/Editor.poll); an
        intent naming an unknown or already-terminal id is dropped with a
        logged warning and no event is committed. No-op on an empty list.
        """
        for intent in intents:
            if intent.action == "plant":
                if not intent.name.strip():
                    logger.warning("%s: dropped thread plant intent with empty name", self.name)
                    continue
                thread_id = slugify_thread_name(intent.name)
                if thread_id in active_thread_ids:
                    # A thread id is minted exactly once, at thread.planted. This
                    # plant collides with an id that's already live, so the agent
                    # clearly means "this thread is live" — downgrade to a touch
                    # instead of committing a planted event the projection would
                    # just no-op.
                    logger.info(
                        "%s: plant %r collides with active thread id %r, downgrading to touch",
                        self.name, intent.name, thread_id,
                    )
                    await self._committer.commit(
                        self.name, EventType.THREAD_TOUCHED, thread_id,
                        ThreadTouched(id=thread_id, chapter_id=chapter_id, note=intent.note),
                    )
                    continue
                logger.warning(
                    "%s: plant %r mints id %r; if this id already exists (terminal or unknown "
                    "to the caller) the commit will be a projection no-op",
                    self.name, intent.name, thread_id,
                )
                await self._committer.commit(
                    self.name, EventType.THREAD_PLANTED, thread_id,
                    ThreadPlanted(id=thread_id, name=intent.name, chapter_id=chapter_id, note=intent.note),
                )
                continue
            if intent.id not in active_thread_ids:
                logger.warning(
                    "%s: dropped thread %s intent for unknown id %r", self.name, intent.action, intent.id
                )
                continue
            payload_cls, event_type = {
                "touch": (ThreadTouched, EventType.THREAD_TOUCHED),
                "pay_off": (ThreadPaidOff, EventType.THREAD_PAID_OFF),
                "abandon": (ThreadAbandoned, EventType.THREAD_ABANDONED),
            }[intent.action]
            await self._committer.commit(
                self.name, event_type, intent.id,
                payload_cls(id=intent.id, chapter_id=chapter_id, note=intent.note),
            )
