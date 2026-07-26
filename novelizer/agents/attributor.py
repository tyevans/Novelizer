"""Formalize the Author's inline speaker markup into structured attribution.

The markers are unambiguous by construction, so this agent is deterministic in
the common case and never calls the model: parse, resolve names to ids, commit
clean prose plus segments. The model is reached for only when the parser
reports markup it cannot make sense of, and a failed repair still commits what
was parsed -- a typo in one tag must not cost a chapter its attribution.

Backlog is a pure log fold (chapter.attributed vs. chapter.created/revised),
matching the Summarizer's fingerprint/watermark shape: readiness gates on
_gate_on_watermark, so _run must record (or clear) the watermark the same way.
"""
from __future__ import annotations

import logging

from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import FlagDraft, RepairedMarkup
from novelizer.brain.watermarks import current_done_ids
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import AttributedSegment, ChapterAttributed, EventType
from novelizer.canon.flags import own_rejections
from novelizer.canon.read_store import ReadStore
from novelizer.speech.markers import parse_markers
from novelizer.speech.resolve import build_name_index, resolve_speaker
from novelizer.speech.segments import NARRATION, segment_prose
from novelizer.store.models import FlagStatus

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You repair malformed speaker markup in prose. The prose uses
<speech char="Name">...</speech> for spoken dialogue and
<thought char="Name">...</thought> for rendered interior thought.

You are shown prose whose markup is broken -- an unclosed tag, a nested tag, a
malformed attribute. Return the SAME prose with the markup corrected: close what
is open, unnest what is nested, and leave every character of the actual prose
untouched. Never add, remove or reword prose. Never invent a speaker: if you
cannot tell who speaks, drop the tag and leave the text bare."""

FLAG_CATEGORY = "attribution"


class Attributor(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        event_store: EventStore,
        interval: int = 120,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval,
                         name="attributor", personality=personality)
        self._events = event_store

    async def _unattributed(self) -> list:
        chapters = await self._read.list_chapters()
        done = current_done_ids(
            await self._events.events_since(0, event_types=[EventType.CHAPTER_ATTRIBUTED]),
            await self._events.events_since(0, event_types=[EventType.CHAPTER_REVISED]),
        )
        return [c for c in chapters if c.id not in done]

    async def _fingerprint(self) -> tuple:
        chapters = await self._read.list_chapters()
        pending = await self._unattributed()
        return (len(chapters), chapters[-1].id if chapters else "", len(pending))

    async def readiness(self) -> float:
        pending = await self._unattributed()
        if not pending:
            return 0.0
        return await self._gate_on_watermark(min(1.0, len(pending) / 3))

    async def poll(self) -> dict:
        return {"pending": await self._unattributed()}

    async def work(self, ctx: dict) -> dict:
        index = build_name_index(await self._read.list_characters())
        results: dict[str, tuple[ChapterAttributed, list[str]]] = {}
        for chapter in ctx["pending"]:
            results[chapter.id] = await self._attribute(chapter, index)
        return results

    async def _attribute(self, chapter, index) -> tuple[ChapterAttributed, list[str]]:
        parsed = parse_markers(chapter.prose)
        if parsed.problems:
            repaired = await self._repair(chapter.prose)
            if repaired is not None:
                reparsed = parse_markers(repaired)
                if not reparsed.problems:
                    parsed = reparsed

        segments: list[AttributedSegment] = []
        unresolved: list[str] = []
        for seg in segment_prose(parsed.clean_prose, parsed.spans):
            character_id = None
            if seg.kind != NARRATION:
                character_id = resolve_speaker(seg.char_name, index)
                if character_id is None:
                    unresolved.append(seg.char_name)
            segments.append(AttributedSegment(
                index=seg.index, kind=seg.kind, character_id=character_id,
                character_name=seg.char_name, start_offset=seg.start,
                end_offset=seg.end, text=seg.text,
            ))

        problems = list(parsed.problems)
        for name in sorted(set(unresolved)):
            problems.append(f"unresolved speaker {name!r}")
        payload = ChapterAttributed(
            chapter_id=chapter.id, prose=parsed.clean_prose,
            segments=segments, problems=problems,
        )
        return payload, problems

    async def _repair(self, marked: str) -> str | None:
        try:
            result = await self._runner.ainvoke(
                {"messages": [{"role": "user", "content": marked}]}
            )
        except Exception:
            logger.warning("%s: repair call raised; committing the parsed result as-is",
                           self.name, exc_info=True)
            return None
        out = result.get("structured_response")
        if not isinstance(out, RepairedMarkup) or not out.prose.strip():
            logger.warning("%s: no usable repair (%r); committing the parsed result as-is",
                           self.name, type(out).__name__)
            return None
        return out.prose

    async def commit(self, results: dict, ctx: dict) -> None:
        # _commit_flag_drafts dedupes only against currently OPEN flags, and
        # `problems` is recomputed from the prose every pass -- so without
        # this, a finding Triage rejected (e.g. "unresolved speaker 'Nobody'
        # is intentional") would be re-filed verbatim the next time this
        # chapter is (re)attributed, forever. This agent files in plain code
        # rather than by model judgement, so it cannot read its own
        # rejections from a prompt the way an LLM-driven filer does
        # (BaseAgent._own_rejections_note); honouring them here, before
        # drafting, is the code-side equivalent -- see own_rejections.
        rejected = own_rejections(
            await self._read.list_flags(category=FLAG_CATEGORY, status=FlagStatus.rejected),
            filed_by=self.name,
        )
        rejected_descriptions = {f.description for f in rejected}

        drafts: list[FlagDraft] = []
        for chapter_id, (payload, problems) in results.items():
            await self._committer.commit(
                self.name, EventType.CHAPTER_ATTRIBUTED, chapter_id, payload,
            )
            for problem in problems:
                description = f"chapter {chapter_id}: {problem}"
                if description in rejected_descriptions:
                    continue
                drafts.append(FlagDraft(
                    category=FLAG_CATEGORY,
                    description=description,
                    related_entry_ids=[chapter_id],
                ))
        await self._commit_flag_drafts(drafts, FLAG_CATEGORY)

    async def _run(self) -> None:
        fp_seen = await self._fingerprint()
        ctx = await self.poll()
        if not ctx["pending"]:
            self.note_pass()
            return
        results = await self.work(ctx)
        await self.commit(results, ctx)
        fp_now = await self._fingerprint()
        # Same contract as the Summarizer: pending > 0 at record time means a
        # revision arrived mid-run (or, in principle, a failed pass -- this
        # agent has none, since parse/resolve/commit never fails); a moved
        # chapter-components pair means prose arrived mid-run. Either way
        # leave the watermark clear so the next tick re-dispatches. Own
        # chapter.attributed commits are absorbed via fp_now otherwise.
        if fp_now[2] == 0 and fp_now[:2] == fp_seen[:2]:
            self._last_fingerprint = fp_now
        else:
            self._clear_watermark()


def build_attributor_runner(settings, callbacks=None):
    from agent_kit import build_chat_model
    from deepagents import create_deep_agent
    # Repair is transcription, not composition: run cold.
    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        temperature=0.0, max_tokens=settings.llm_max_tokens, callbacks=callbacks,
    )
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT,
                             response_format=RepairedMarkup)


from novelizer.agents.registry_types import AgentContext, AgentSpec, AgentTier


def _construct(ctx: AgentContext) -> Attributor:
    runner = ctx.runner_for("attributor", build_attributor_runner)
    return Attributor(
        runner, ctx.read, ctx.committer, ctx.events,
        personality=ctx.personalities.get("attributor", ""),
    )


SPEC = AgentSpec(name="attributor", tool_grant=None, construct=_construct,
                 tier=AgentTier.FULL)
