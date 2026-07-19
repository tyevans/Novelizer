from __future__ import annotations
import logging
import time
import uuid
from typing import Protocol
from pydantic import BaseModel, Field
from novelizer.canon.events import (
    EventType, AgentRemark, ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned,
    SecretCreated, SecretLearned, SecretReferenced, SecretRevealed, CausalEdgeDeclared,
)
from novelizer.canon.threads import slugify_thread_name
from novelizer.canon.secrets import slugify_secret_name
from novelizer.agents.schemas import ThreadIntent, KnowledgeIntent, CausalIntent
from novelizer.run_context import current_run_id, current_agent_name
from novelizer.telemetry.events import (
    TelemetryEventType, AgentRunStarted, AgentRunFinished, AgentRunFailed,
)

logger = logging.getLogger(__name__)

_KNOWLEDGE_EVENT_BY_ACTION = {
    "learn": (EventType.SECRET_LEARNED, SecretLearned),
    "reveal": (EventType.SECRET_REVEALED, SecretRevealed),
    "uses": (EventType.SECRET_REFERENCED, SecretReferenced),
}


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)


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
        self.telemetry = None  # TelemetryRecorder; injected by Runtime post-construction

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def ready_for_interval(self, now: float) -> bool:
        return (now - self._last_run) >= self.interval

    def mark_ran(self, now: float) -> None:
        self._last_run = now

    def seconds_until_ready(self, now: float) -> float:
        return max(0.0, self.interval - (now - self._last_run))

    async def readiness(self) -> float:
        return 0.0

    async def _run(self) -> None:
        """Subclasses put their poll/work/commit body here (M-telemetry:
        run_once became a final template that brackets _run with machinery
        events and ambient run context)."""

    async def run_once(self) -> None:
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        rid_token = current_run_id.set(run_id)
        name_token = current_agent_name.set(self.name)
        await self._emit_telemetry(
            TelemetryEventType.AGENT_RUN_STARTED, run_id,
            AgentRunStarted(run_id=run_id, agent_name=self.name),
        )
        try:
            await self._run()
        except Exception as e:
            phase = "llm_call" if (self.telemetry and self.telemetry.in_llm_call(run_id)) else "agent"
            await self._emit_telemetry(
                TelemetryEventType.AGENT_RUN_FAILED, run_id,
                AgentRunFailed(run_id=run_id, agent_name=self.name,
                               error_type=type(e).__name__, error_message=str(e),
                               phase=phase, duration_s=time.monotonic() - started),
            )
            raise
        else:
            await self._emit_telemetry(
                TelemetryEventType.AGENT_RUN_FINISHED, run_id,
                AgentRunFinished(run_id=run_id, agent_name=self.name,
                                 duration_s=time.monotonic() - started),
            )
        finally:
            current_run_id.reset(rid_token)
            current_agent_name.reset(name_token)

    async def _emit_telemetry(self, event_type: str, aggregate_id: str, payload) -> None:
        if self.telemetry is None:
            return
        await self.telemetry.emit(event_type, aggregate_id, payload)

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
                        ThreadTouched(id=thread_id, chapter_id=chapter_id, note=intent.note, source=source),
                    )
                    continue
                logger.warning(
                    "%s: plant %r mints id %r; if this id already exists (terminal or unknown "
                    "to the caller) the commit will be a projection no-op",
                    self.name, intent.name, thread_id,
                )
                await self._committer.commit(
                    self.name, EventType.THREAD_PLANTED, thread_id,
                    ThreadPlanted(id=thread_id, name=intent.name, chapter_id=chapter_id, note=intent.note, source=source),
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
            if payload_cls is ThreadAbandoned:
                payload = payload_cls(id=intent.id, chapter_id=chapter_id, note=intent.note)
            else:
                payload = payload_cls(id=intent.id, chapter_id=chapter_id, note=intent.note, source=source)
            await self._committer.commit(self.name, event_type, intent.id, payload)

    async def _commit_knowledge_intents(
        self,
        intents: list[KnowledgeIntent],
        active_secret_ids: set[str],
        chapter_id: str = "",
        allowed_actions: frozenset[str] = frozenset({"plant", "learn", "reveal", "uses"}),
        source: str = "declared",
    ) -> None:
        """Turn agent-declared KnowledgeIntent entries into secret.* commits.

        `plant` mints a new id via slugify_secret_name(intent.title) and is
        dropped only if the title is blank; a plant colliding with an
        already-known active id is dropped with a warning -- secrets have no
        touch-analog the way threads do (M3.1's plant-collision downgrades
        to thread.touched, which needs only id+note; a colliding secret
        plant can't be safely reinterpreted as learn/reveal/uses without a
        character_id or reveal semantics the plant intent doesn't carry, so
        the safe choice is to drop it, matching how an unknown-id
        learn/reveal/uses intent is dropped below). `learn`/`reveal`/`uses`
        must cite an id present in `active_secret_ids`; `learn`/`uses`
        additionally require a non-blank `character_id`. `allowed_actions`
        restricts which actions this caller may commit -- CharacterKeeper
        passes frozenset({"learn"}) since minting/revealing a secret is a
        narrative-authoring act reserved for Author/Editor (Locked decision
        #1). Any intent whose action is not in `allowed_actions`, or that
        fails validation, is dropped with a logged warning and no event is
        committed. No-op on an empty list.
        """
        for intent in intents:
            if intent.action not in allowed_actions:
                logger.warning(
                    "%s: dropped knowledge intent action %r not permitted for this agent",
                    self.name, intent.action,
                )
                continue
            if intent.action == "plant":
                if not intent.title.strip():
                    logger.warning("%s: dropped secret plant intent with empty title", self.name)
                    continue
                secret_id = slugify_secret_name(intent.title)
                if secret_id in active_secret_ids:
                    logger.warning(
                        "%s: plant %r collides with existing secret id %r, dropping",
                        self.name, intent.title, secret_id,
                    )
                    continue
                await self._committer.commit(
                    self.name, EventType.SECRET_CREATED, secret_id,
                    SecretCreated(id=secret_id, title=intent.title, chapter_id=chapter_id, note=intent.note),
                )
                continue
            if intent.id not in active_secret_ids:
                logger.warning(
                    "%s: dropped knowledge %s intent for unknown secret id %r", self.name, intent.action, intent.id
                )
                continue
            if intent.action in ("learn", "uses") and not intent.character_id.strip():
                logger.warning(
                    "%s: dropped knowledge %s intent with empty character_id", self.name, intent.action
                )
                continue
            event_type, payload_cls = _KNOWLEDGE_EVENT_BY_ACTION[intent.action]
            if intent.action == "reveal":
                payload = payload_cls(id=intent.id, chapter_id=chapter_id, note=intent.note)
            else:
                payload = payload_cls(
                    id=intent.id, character_id=intent.character_id, chapter_id=chapter_id, note=intent.note,
                    source=source,
                )
            await self._committer.commit(self.name, event_type, intent.id, payload)

    async def _commit_causal_intents(
        self, intents: list[CausalIntent], valid_chapter_ids: set[str], source: str = "declared"
    ) -> None:
        """Turn agent-declared CausalIntent entries into
        causal_edge.declared commits.

        Both `cause_chapter_id` and `effect_chapter_id` must be present in
        `valid_chapter_ids`, or the intent is dropped with a logged warning;
        a self-edge (cause == effect) is dropped with a logged warning
        first, since Locked decision #4's ordering-violation check (M4.2)
        needs two distinct chapters to be meaningful. No deduplication: an
        edge has no minted identity and no lifecycle (Locked decision #4),
        so every valid declared edge is committed as its own fact even if
        identical to a prior commit -- the property test in this plan
        (Task 8) asserts replay never drops OR duplicates a declared edge,
        which requires a strict 1:1 event-to-row mapping, not a deduped one.
        No-op on an empty list.
        """
        for intent in intents:
            if intent.cause_chapter_id == intent.effect_chapter_id:
                logger.warning(
                    "%s: dropped self-edge causal intent for chapter %r", self.name, intent.cause_chapter_id
                )
                continue
            if intent.cause_chapter_id not in valid_chapter_ids or intent.effect_chapter_id not in valid_chapter_ids:
                logger.warning(
                    "%s: dropped causal intent citing unknown chapter id(s) %r -> %r",
                    self.name, intent.cause_chapter_id, intent.effect_chapter_id,
                )
                continue
            await self._committer.commit(
                self.name, EventType.CAUSAL_EDGE_DECLARED, intent.effect_chapter_id,
                CausalEdgeDeclared(
                    cause_chapter_id=intent.cause_chapter_id,
                    effect_chapter_id=intent.effect_chapter_id,
                    note=intent.note,
                    source=source,
                ),
            )
