from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import SummarizerOutput
from novelizer.brain.context_assembly import assemble_verbatim
from novelizer.brain.watermarks import current_done_ids
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import ChapterSummarized, EventType
from novelizer.canon.read_store import ReadStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Summarizer for a living fictional world. You produce the rolling
story-so-far record other agents rely on. For the chapter you are shown, return:
- gist: ONE line (at most ~140 characters) naming what happens — the events, not the vibes.
- summary: one paragraph covering the chapter's events, character developments, reveals and
  new locations, in order, so an agent who never reads the prose still knows what is canon.
Work strictly from the prose shown. Never invent, never editorialize, never omit a reveal."""

MERGE_PROMPT = (
    "The chapter was too long for one pass; below are summaries of its consecutive,\n"
    "overlapping parts, in order. Merge them into ONE gist and ONE paragraph summary for\n"
    "the whole chapter, deduplicating the overlap."
)


class Summarizer(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        event_store: EventStore,
        interval: int = 300,
        personality: str = "",
        extractor_token_budget: int = 24000,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="summarizer", personality=personality)
        self._events = event_store
        self._budget = extractor_token_budget

    async def _unsummarized(self) -> list:
        chapters = await self._read.list_chapters()
        done = current_done_ids(
            await self._events.events_since(0, event_types=[EventType.CHAPTER_SUMMARIZED]),
            await self._events.events_since(0, event_types=[EventType.CHAPTER_REVISED]),
        )
        return [c for c in chapters if c.id not in done]

    async def readiness(self) -> float:
        pending = await self._unsummarized()
        if not pending:
            return 0.0
        return await self._gate_on_watermark(0.6)

    async def _fingerprint(self) -> tuple:
        chapters = await self._read.list_chapters()
        pending = await self._unsummarized()
        return (len(chapters), chapters[-1].id if chapters else "", len(pending))

    async def poll(self) -> dict:
        return {"pending": await self._unsummarized()}

    async def work(self, ctx: dict) -> dict[str, SummarizerOutput]:
        results: dict[str, SummarizerOutput] = {}
        for chapter in ctx["pending"]:
            out = await self._summarize_chapter(chapter)
            if out is not None:
                results[chapter.id] = out
        return results

    async def _summarize_chapter(self, chapter) -> SummarizerOutput | None:
        windows = assemble_verbatim(chapter.prose, self._budget)
        parts: list[str] = []
        for w in windows:
            label = f"Chapter '{chapter.title}'" + (
                f" (part {w.index + 1}/{w.total})" if w.total > 1 else ""
            )
            out = await self._call(f"{label}:\n{w.text}")
            if out is None:
                # miner convention: unstamped, retried next poll
                return None
            if w.total == 1:
                return out
            parts.append(out.summary)
        merged = await self._call(MERGE_PROMPT + "\n\n" + "\n\n".join(parts))
        return merged

    async def _call(self, msg: str) -> SummarizerOutput | None:
        try:
            result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        except Exception:
            logger.warning("%s: summarize call raised; will retry next poll", self.name, exc_info=True)
            return None
        out = result.get("structured_response")
        if not isinstance(out, SummarizerOutput):
            logger.warning("%s: no usable structured response (%r); will retry next poll",
                           self.name, type(out).__name__)
            return None
        return out

    async def commit(self, results: dict[str, SummarizerOutput], ctx: dict) -> None:
        for chapter_id, out in results.items():
            await self._committer.commit(
                self.name, EventType.CHAPTER_SUMMARIZED, chapter_id,
                ChapterSummarized(chapter_id=chapter_id, gist=out.gist, summary=out.summary),
            )

    async def _run(self) -> None:
        ctx = await self.poll()
        if not ctx["pending"]:
            self.note_pass()
            return
        results = await self.work(ctx)
        await self.commit(results, ctx)
        await self._record_watermark()


def build_summarizer_runner(settings, callbacks=None):
    from deepagents import create_deep_agent
    from langchain.agents.structured_output import ProviderStrategy
    from novelizer.agents.llm import build_chat_model
    # Summarization is extraction, not composition: run cold, grammar-constrained
    # (same rationale as the continuity mining runner).
    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        temperature=0.2, max_tokens=settings.llm_max_tokens, callbacks=callbacks,
    )
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT,
                             response_format=ProviderStrategy(SummarizerOutput))


from novelizer.agents.registry_types import AgentContext, AgentSpec


def _construct(ctx: AgentContext) -> Summarizer:
    runner = ctx.runner_for("summarizer", build_summarizer_runner)
    return Summarizer(
        runner, ctx.read, ctx.committer, ctx.events,
        interval=ctx.settings.summarizer_interval,
        personality=ctx.personalities.get("summarizer", ""),
        extractor_token_budget=ctx.settings.extractor_token_budget,
    )


SPEC = AgentSpec(name="summarizer", tool_grant=None, construct=_construct)
