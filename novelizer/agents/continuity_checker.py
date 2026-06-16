from __future__ import annotations
import json
from novelizer.agents.base import BaseAgent, AgentState
from novelizer.store.models import RetconRequest
from novelizer.store.queries import Store

SYSTEM_PROMPT = """You are the Continuity Checker for a living fictional world.
Review the provided world entries, characters, and chapter excerpts for contradictions,
anachronisms, or logical inconsistencies.

Respond with JSON:
  "retcon_requests": list of objects, each with:
    "description": string (what contradicts what),
    "conflicting_entry_ids": list of strings (ids of conflicting records),
    "proposed_resolution": string (how to resolve it)

If no contradictions found, respond with {"retcon_requests": []}.
Respond with ONLY the JSON object."""


class ContinuityChecker(BaseAgent):
    def __init__(self, store: Store, min_interval: int = 900, llm_model: str = "llama3.2") -> None:
        super().__init__(name="continuity_checker", store=store, min_interval=min_interval, llm_model=llm_model)

    async def readiness_check(self) -> float:
        open_retcons = await self.store.db.count_open_retcons()
        return max(0.1, 1.0 - (open_retcons / 5))

    async def poll(self, state: AgentState) -> None:
        state.context["world_entries"] = await self.store.list_world_entries()
        state.context["characters"] = await self.store.list_characters()
        chapters = await self.store.list_chapters()
        state.context["chapters"] = chapters[-10:]

    async def work(self, state: AgentState) -> None:
        entries = state.context["world_entries"]
        chars = state.context["characters"]
        chapters = state.context["chapters"]

        entry_text = "\n".join(f"[{e.id[:8]}] {e.title}: {e.body[:200]}" for e in entries[:20])
        char_text = "\n".join(f"[{c.id[:8]}] {c.name}: {c.traits}" for c in chars[:10])
        chapter_text = "\n".join(f"[{ch.id[:8]}] {ch.title}: {ch.prose[:300]}" for ch in chapters)

        user_msg = (
            f"World entries:\n{entry_text or 'None.'}\n\n"
            f"Characters:\n{char_text or 'None.'}\n\n"
            f"Recent chapters:\n{chapter_text or 'None.'}"
        )
        raw = await self._llm([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ])
        try:
            data = json.loads(raw)
            state.context["retcon_requests"] = [
                RetconRequest(**r) for r in data.get("retcon_requests", [])
            ]
        except (json.JSONDecodeError, TypeError, ValueError):
            state.context["retcon_requests"] = []

    async def commit(self, state: AgentState) -> None:
        for req in state.context.get("retcon_requests", []):
            await self.store.save_retcon_request(req)
