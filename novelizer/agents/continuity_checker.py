from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import ContinuityOutput
from novelizer.brain.leaks import find_leaks, leak_description
from novelizer.brain.paradoxes import find_paradoxes, paradox_description
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import RetconRequest, RetconStatus

SYSTEM_PROMPT = """You are the Continuity Checker for a living fictional world. Review the given world
entries, characters, and chapter excerpts for contradictions, anachronisms, or logical inconsistencies.
Return retcon_requests, each with a description (what contradicts what), conflicting_entry_ids (the ids
of the conflicting records), and a proposed_resolution. Return an empty list if you find nothing."""


class ContinuityChecker(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 900,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="continuity_checker", personality=personality)

    async def readiness(self) -> float:
        open_retcons = len(await self._read.list_retcon_requests(status=RetconStatus.open))
        return max(0.1, 1.0 - open_retcons / 5)

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "chapters": chapters[-10:],
            "chapter_order": [c.id for c in chapters],
            "secret_references": await self._read.list_secret_references(),
            "knowledge_matrix": await self._read.knowledge_matrix(),
            "causal_edges": await self._read.list_causal_edges(),
        }

    async def work(self, ctx: dict) -> ContinuityOutput | None:
        world = "\n".join(f"[{e.id[:8]}] {e.title}: {e.body[:200]}" for e in ctx["world"][:20]) or "None."
        chars = "\n".join(f"[{c.id[:8]}] {c.name}: {c.traits}" for c in ctx["characters"][:10]) or "None."
        chapters = "\n".join(f"[{c.id[:8]}] {c.title}: {c.prose[:300]}" for c in ctx["chapters"]) or "None."
        cast = f"\n\nIn character: {self.personality}" if self.personality else ""
        msg = f"World entries:\n{world}\n\nCharacters:\n{chars}\n\nRecent chapters:\n{chapters}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: ContinuityOutput | None, ctx: dict) -> None:
        open_reqs = await self._read.list_retcon_requests(status=RetconStatus.open)
        seen_descriptions = {r.description for r in open_reqs}

        if out is not None:
            for r in out.retcon_requests:
                req = RetconRequest(description=r.description, conflicting_entry_ids=r.conflicting_entry_ids,
                                    proposed_resolution=r.proposed_resolution)
                await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)
            await self._remark(out.feed_note)

        for leak in find_leaks(ctx.get("secret_references", []), ctx.get("knowledge_matrix", {})):
            description = leak_description(leak)
            if description in seen_descriptions:
                continue
            seen_descriptions.add(description)
            req = RetconRequest(
                description=description,
                conflicting_entry_ids=[leak.secret_id, leak.character_id, leak.chapter_id],
                proposed_resolution="Review whether the reference should be removed or a learn/reveal event added.",
            )
            await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)

        for paradox in find_paradoxes(ctx.get("causal_edges", []), ctx.get("chapter_order", [])):
            description = paradox_description(paradox)
            if description in seen_descriptions:
                continue
            seen_descriptions.add(description)
            req = RetconRequest(
                description=description,
                conflicting_entry_ids=[paradox.cause_chapter_id, paradox.effect_chapter_id],
                proposed_resolution="Review the causal edge for an ordering or cycle correction.",
            )
            await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)

    async def run_once(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_continuity_checker_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=ContinuityOutput)
