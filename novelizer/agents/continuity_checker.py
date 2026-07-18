from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import ContinuityOutput
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import RetconRequest, RetconStatus

SYSTEM_PROMPT = """You are the Continuity Checker for a living fictional world. Review the given world
entries, characters, and chapter excerpts for contradictions, anachronisms, or logical inconsistencies.
Return retcon_requests, each with a description (what contradicts what), conflicting_entry_ids (the ids
of the conflicting records), and a proposed_resolution. Return an empty list if you find nothing."""


class ContinuityChecker(BaseAgent):
    def __init__(self, runner: Runner, read_store: ReadStore, committer: Committer, interval: int = 900) -> None:
        super().__init__(runner, read_store, committer, interval, name="continuity_checker")

    async def readiness(self) -> float:
        open_retcons = len(await self._read.list_retcon_requests(status=RetconStatus.open))
        return max(0.1, 1.0 - open_retcons / 5)

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "chapters": chapters[-10:],
        }

    async def work(self, ctx: dict) -> ContinuityOutput | None:
        world = "\n".join(f"[{e.id[:8]}] {e.title}: {e.body[:200]}" for e in ctx["world"][:20]) or "None."
        chars = "\n".join(f"[{c.id[:8]}] {c.name}: {c.traits}" for c in ctx["characters"][:10]) or "None."
        chapters = "\n".join(f"[{c.id[:8]}] {c.title}: {c.prose[:300]}" for c in ctx["chapters"]) or "None."
        msg = f"World entries:\n{world}\n\nCharacters:\n{chars}\n\nRecent chapters:\n{chapters}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: ContinuityOutput | None, ctx: dict) -> None:
        if out is None:
            return
        for r in out.retcon_requests:
            req = RetconRequest(description=r.description, conflicting_entry_ids=r.conflicting_entry_ids,
                                proposed_resolution=r.proposed_resolution)
            await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)

    async def run_once(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_continuity_checker_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=ContinuityOutput)
