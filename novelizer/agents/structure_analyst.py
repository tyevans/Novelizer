from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import StructureAnalystOutput
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, AnnotationStructureScored

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Structure Analyst for a living fictional world's story.
You read recent unscored chapters and score each one's narrative tension and pacing.
For each chapter, return its id, a tension score from 0.0 (slack) to 1.0 (peak intensity),
and a short pacing_label (e.g. "rising", "climax", "lull", "steady").
Return one entry per chapter you were given, no more."""

_BATCH_SIZE = 5
_READINESS_DIVISOR = 3


class StructureAnalyst(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 180,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="structure_analyst", personality=personality)

    async def _unscored_recent_chapters(self) -> list:
        chapters = await self._read.list_chapters()
        scored_ids = {s.chapter_id for s in await self._read.list_structure_scores()}
        unscored = [c for c in chapters if c.id not in scored_ids]
        return unscored[-_BATCH_SIZE:]

    async def readiness(self) -> float:
        unscored = await self._unscored_recent_chapters()
        if not unscored:
            return 0.0
        return min(1.0, len(unscored) / _READINESS_DIVISOR)

    async def poll(self) -> dict:
        return {"unscored": await self._unscored_recent_chapters()}

    async def work(self, ctx: dict) -> StructureAnalystOutput | None:
        chapters = ctx["unscored"]
        if not chapters:
            return None
        listing = "\n\n".join(f"Chapter id:{c.id} '{c.title}': {c.prose[:400]}" for c in chapters)
        cast = self._guarded_line("In character", self.personality)
        msg = f"Score these chapters:\n{listing}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: StructureAnalystOutput | None, ctx: dict) -> None:
        if out is None:
            return
        valid_ids = {c.id for c in ctx["unscored"]}
        for score in out.scores:
            if score.chapter_id not in valid_ids:
                logger.warning(
                    "structure_analyst: dropped score for unrequested chapter id %r", score.chapter_id
                )
                continue
            payload = AnnotationStructureScored(
                chapter_id=score.chapter_id, tension=score.tension, pacing_label=score.pacing_label
            )
            await self._committer.commit(self.name, EventType.ANNOTATION_STRUCTURE_SCORED, score.chapter_id, payload)
        await self._remark(out.feed_note)

    async def _run(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_structure_analyst_runner(settings, callbacks=None, backend=None, tools=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=StructureAnalystOutput,
            backend=backend, tools=tools,
        )
        config = {"recursion_limit": 50}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=StructureAnalystOutput)
