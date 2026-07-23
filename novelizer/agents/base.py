from __future__ import annotations
import logging
import time
import uuid
from typing import Protocol
from pydantic import BaseModel, Field
from novelizer.agents import prompts
from novelizer.canon.events import EventType, AgentRemark
from novelizer.agents.schemas import (
    ThreadIntent, KnowledgeIntent, CausalIntent, ThemeIntent, PromiseIntent,
    BlueprintPlan, BriefIntent, BeatIntent, ResolutionPlanIntent, ArcIntent, FlagDraft,
)
from novelizer.store.models import ChapterBriefRecord, Flag, FlagStatus
from novelizer.agents import intents as intent_helpers
from agent_kit import current_run_id, current_agent_name
from novelizer.telemetry.events import (
    TelemetryEventType, AgentRunStarted, AgentRunFinished, AgentRunFailed,
)

logger = logging.getLogger(__name__)

# Tool-heavy passes (canon pulls + edits) can exceed LangGraph's default of 25;
# 50 still tripped in practice, so give agent graphs generous headroom.
GRAPH_RECURSION_LIMIT = 100

# An agent that ran on fresh material but explicitly chose not to act steps
# back for this many intervals instead of one, freeing dispatch slots.
PASS_BACKOFF_MULTIPLIER = 3
# Re-exported from novelizer.agents.prompts, which is the real home: three agents
# still import these through here. Migrate those imports, then drop this.
DEFAULT_PASS_REMARK = prompts.DEFAULT_PASS_REMARK
PASS_PROMPT_INSTRUCTION = prompts.PASS_PROMPT_INSTRUCTION


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)
    theme_intents: list[ThemeIntent] = Field(default_factory=list)
    promise_intents: list[PromiseIntent] = Field(default_factory=list)
    flags: list[FlagDraft] = Field(default_factory=list)
    """A concern the Author hit while drafting that it can't resolve itself —
    a brief contradicting a voice card, a beat it can't service, a promise
    with nowhere natural to land. Filed as Flag(category=...) at commit time
    via BaseAgent, same pipeline every other judgment-making agent uses."""


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
        self._backoff_until = 0.0
        self._last_fingerprint: tuple | None = None
        self.telemetry = None  # TelemetryRecorder; injected by Runtime post-construction

    @staticmethod
    def _guarded_line(label: str, value: str) -> str:
        """Return an optional "\n\n{label}: {value}" line, or "" if value is falsy."""
        return f"\n\n{label}: {value}" if value else ""

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def ready_for_interval(self, now: float) -> bool:
        return (now - self._last_run) >= self.interval and now >= self._backoff_until

    def mark_ran(self, now: float) -> None:
        self._last_run = now

    def seconds_until_ready(self, now: float) -> float:
        return max(0.0, self.interval - (now - self._last_run), self._backoff_until - now)

    def note_pass(self, now: float | None = None) -> None:
        """Record an explicit "nothing to do" verdict: back off for
        PASS_BACKOFF_MULTIPLIER intervals instead of one. Same clock family
        as the scheduler's default (time.monotonic)."""
        if now is None:
            now = time.monotonic()
        self._backoff_until = now + self.interval * PASS_BACKOFF_MULTIPLIER

    async def _fingerprint(self) -> tuple | None:
        """External story state this agent's work depends on. None (default)
        disables watermarking. Subclasses return a small tuple; captured
        AFTER the agent's own commits, so its own writes never re-trigger it."""
        return None

    async def _gate_on_watermark(self, score: float) -> float:
        fp = await self._fingerprint()
        if fp is not None and fp == self._last_fingerprint:
            return 0.0
        return score

    async def _record_watermark(self) -> None:
        self._last_fingerprint = await self._fingerprint()

    def _clear_watermark(self) -> None:
        self._last_fingerprint = None

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

    async def _commit_flag_drafts(self, drafts: list[FlagDraft], category: str) -> None:
        """File FlagDrafts as Flag(category=category, filed_by=self.name), deduped
        by description against the currently open queue in that category — the
        pattern Continuity Checker's mined-flag filing and the Editor's craft/
        voice-drift flags all follow, since an agent re-polling the same target
        every cycle would otherwise re-file the same finding each pass."""
        if not drafts:
            return
        open_flags = await self._read.list_flags(category=category, status=FlagStatus.open)
        seen_descriptions = {f.description for f in open_flags}
        for draft in drafts:
            if draft.description in seen_descriptions:
                continue
            seen_descriptions.add(draft.description)
            flag = Flag(category=category, filed_by=self.name, description=draft.description,
                        related_entry_ids=draft.related_entry_ids, proposed_resolution=draft.proposed_resolution)
            await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)

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
        embedding_store=None,
    ) -> None:
        await intent_helpers.commit_theme_intents(
            self._committer, self.name, intents, active_theme_ids, chapter_id=chapter_id, source=source,
            embedding_store=embedding_store, read_store=self._read,
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

    async def _commit_promise_intents(
        self,
        intents: list[PromiseIntent],
        active_promise_ids: set[str],
        active_thread_ids: set[str],
        chapter_id: str = "",
        source: str = "declared",
    ) -> None:
        await intent_helpers.commit_promise_intents(
            self._committer, self.name, intents, active_promise_ids, active_thread_ids,
            chapter_id=chapter_id, source=source,
        )

    async def _commit_arc_intents(
        self,
        intents: list[ArcIntent],
        active_arc_ids: set[str],
        character_ids: set[str],
        active_beat_ids: set[str],
        chapter_id: str = "",
    ) -> None:
        await intent_helpers.commit_arc_intents(
            self._committer, self.name, intents, active_arc_ids, character_ids, active_beat_ids,
            chapter_id=chapter_id,
        )

    async def _commit_blueprint_plan(self, plan: BlueprintPlan | None) -> None:
        await intent_helpers.commit_blueprint_plan(self._committer, self.name, plan)

    async def _commit_retarget_intent(self, intent, blueprint) -> None:
        await intent_helpers.commit_retarget_intent(self._committer, self.name, intent, blueprint)

    async def _commit_brief_intents(
        self,
        intents: list[BriefIntent],
        open_brief_ids: list[ChapterBriefRecord],
        drafted_chapter_count: int,
        active_thread_ids: set[str],
        active_beat_ids: set[str],
        active_promise_ids: set[str],
    ) -> None:
        await intent_helpers.commit_brief_intents(
            self._committer, self.name, intents, open_brief_ids, drafted_chapter_count,
            active_thread_ids, active_beat_ids, active_promise_ids,
        )

    async def _commit_beat_intents(
        self,
        intents: list[BeatIntent],
        active_beat_ids: set[str],
        valid_chapter_ids: set[str],
    ) -> None:
        await intent_helpers.commit_beat_intents(
            self._committer, self.name, intents, active_beat_ids, valid_chapter_ids
        )

    async def _commit_resolution_plan_intents(
        self,
        intents: list[ResolutionPlanIntent],
        active_thread_ids: set[str],
        unrevealed_secret_ids: set[str],
    ) -> None:
        await intent_helpers.commit_resolution_plan_intents(
            self._committer, self.name, intents, active_thread_ids, unrevealed_secret_ids
        )
