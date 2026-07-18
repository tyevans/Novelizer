from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import KeeperOutput
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import RetconRequest

SYSTEM_PROMPT = """You are the Character Keeper for a living fictional world.
You receive characters (with traits and arcs) and recent prose chapters. Your tasks:
1. Update each character's arc_status to reflect what recent chapters show.
2. Flag behavioral contradictions between a character's defined traits and their actions.
Return updated_characters (id + revised arc_status, and any corrected traits/motivations/backstory)
and retcon_requests (description, conflicting_entry_ids, proposed_resolution)."""


class CharacterKeeper(BaseAgent):
    def __init__(self, runner: Runner, read_store: ReadStore, committer: Committer, interval: int = 120) -> None:
        super().__init__(runner, read_store, committer, interval, name="character_keeper")

    async def readiness(self) -> float:
        chars = await self._read.list_characters()
        chapters = await self._read.list_chapters()
        return 0.5 if (chars and chapters) else 0.2

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {"characters": await self._read.list_characters(), "recent": chapters[-5:]}

    async def work(self, ctx: dict) -> KeeperOutput | None:
        if not ctx["characters"]:
            return None
        chars = "\n".join(f"- {c.name} (id:{c.id}): traits={c.traits}, arc={c.arc_status}" for c in ctx["characters"])
        chapters = "\n\n".join(f"Chapter '{c.title}': {c.prose[:300]}" for c in ctx["recent"]) or "None."
        msg = f"Characters:\n{chars}\n\nRecent chapters:\n{chapters}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: KeeperOutput | None, ctx: dict) -> None:
        if out is None:
            return
        for upd in out.updated_characters:
            current = await self._read.get_character(upd.id)
            if current is None:
                continue
            fields = {"arc_status": upd.arc_status}
            for f in ("traits", "motivations", "backstory"):
                v = getattr(upd, f)
                if v is not None:
                    fields[f] = v
            updated = current.model_copy(update=fields)
            await self._committer.commit(self.name, EventType.CHARACTER_UPDATED, updated.id, updated)
        for r in out.retcon_requests:
            req = RetconRequest(description=r.description, conflicting_entry_ids=r.conflicting_entry_ids,
                                proposed_resolution=r.proposed_resolution)
            await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)

    async def run_once(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_character_keeper_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=KeeperOutput)
