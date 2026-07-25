from __future__ import annotations

from agent_kit import BaseAgent as _KitBaseAgent, Runner  # noqa: F401 — Runner re-exported for agent imports
from pydantic import BaseModel, Field
from novelizer.brain.context import rejected_flags_note
from novelizer.canon.events import EventType, AgentRemark
from novelizer.agents.schemas import (
    ThreadIntent, SecretPlant, SecretCitation, CausalIntent, ThemeIntent, PromiseIntent,
    BlueprintPlan, BriefIntent, BeatIntent, ResolutionPlanIntent, ArcIntent, FlagDraft,
)
from novelizer.store.models import ChapterBriefRecord, Flag, FlagStatus
from novelizer.agents import intents as intent_helpers


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    secret_plants: list[SecretPlant] = Field(default_factory=list)
    secret_citations: list[SecretCitation] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)
    theme_intents: list[ThemeIntent] = Field(default_factory=list)
    promise_intents: list[PromiseIntent] = Field(default_factory=list)
    flags: list[FlagDraft] = Field(default_factory=list)
    """A concern the Author hit while drafting that it can't resolve itself —
    a brief contradicting a voice card, a beat it can't service, a promise
    with nowhere natural to land. Filed as Flag(category=...) at commit time
    via BaseAgent, same pipeline every other judgment-making agent uses."""


class BaseAgent(_KitBaseAgent):
    """Novelizer's agent chassis: agent_kit's loop (intervals, backoff,
    watermarking, run_once telemetry bracketing) plus the fiction-side
    read/commit surface every novelizer agent shares."""

    def __init__(
        self,
        runner,
        read_store,
        committer,
        interval: int,
        name: str | None = None,
        personality: str = "",
    ) -> None:
        super().__init__(runner, interval, name=name, personality=personality)
        self._read = read_store
        self._committer = committer

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

    async def _own_rejections_note(self) -> str:
        """This agent's own recent rejections, as a prompt block (empty when none).

        The read half of `filed_by`, which every filing site wrote and nothing
        read: an agent could not learn a judgement of its own had been thrown
        out. It lives beside `_commit_flag_drafts` because the write side does,
        and on the chassis rather than in each agent because seven agents file
        flags — the bound and the wording are one decision, not seven.
        """
        return rejected_flags_note(
            await self._read.list_flags(status=FlagStatus.rejected), self.name
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

    async def _commit_secret_plants(
        self,
        plants: list[SecretPlant],
        active_secret_ids: set[str],
        chapter_id: str = "",
    ) -> None:
        await intent_helpers.commit_secret_plants(
            self._committer, self.name, plants, active_secret_ids, chapter_id=chapter_id,
        )

    async def _commit_secret_citations(
        self,
        citations: list[SecretCitation],
        active_secret_ids: set[str],
        chapter_id: str = "",
        allowed_actions: frozenset[str] = frozenset({"learn", "reveal", "uses"}),
        source: str = "declared",
        character_ids: set[str] | None = None,
    ) -> None:
        await intent_helpers.commit_secret_citations(
            self._committer, self.name, citations, active_secret_ids, chapter_id=chapter_id,
            allowed_actions=allowed_actions, source=source, character_ids=character_ids,
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
