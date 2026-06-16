from __future__ import annotations
import json
from novelizer.agents.base import BaseAgent, AgentState
from novelizer.store.models import WorldEntry, Domain
from novelizer.store.queries import Store

SYSTEM_PROMPT = """You are the World Architect for an ever-expanding fictional world.
Your job is to generate new lore, geography, factions, history, and cosmology.
You receive a summary of what already exists and identify gaps or thin areas.
Respond with a JSON array of new world entries. Each entry must have:
  "title": string,
  "body": string (2-4 paragraphs of rich lore),
  "domain": one of [physical, social, metaphysical, historical, other],
  "tags": list of strings

Generate 1-3 entries that expand underrepresented or unexplored aspects of the world.
Respond with ONLY the JSON array, no other text."""


class WorldArchitect(BaseAgent):
    def __init__(self, store: Store, min_interval: int = 120, llm_model: str = "llama3.2") -> None:
        super().__init__(name="world_architect", store=store, min_interval=min_interval, llm_model=llm_model)

    async def readiness_check(self) -> float:
        count = await self.store.db.count_world_entries()
        # Always ready, but less urgent as world fills out (asymptotic toward 0.2)
        return max(0.2, 1.0 - (count / 50))

    async def poll(self, state: AgentState) -> None:
        entries = await self.store.list_world_entries()
        state.context["existing_entries"] = entries

    async def work(self, state: AgentState) -> None:
        entries = state.context["existing_entries"]
        summary_lines = [f"- [{e.domain}] {e.title}: {e.body[:100]}..." for e in entries[:20]]
        summary = "\n".join(summary_lines) if summary_lines else "The world is empty. Start from scratch."

        signals = await self.store.list_unconsumed_signals(target_agent=self.name)
        seed_text = ""
        for sig in signals:
            seed_text += f"\nDirector seed: {sig.body}"
            await self.store.consume_signal(sig.id)

        user_msg = f"Existing world entries:\n{summary}\n{seed_text}\n\nGenerate new world entries."
        raw = await self._llm([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ])
        try:
            data = json.loads(raw)
            state.context["new_entries"] = [WorldEntry(**item) for item in data]
        except (json.JSONDecodeError, TypeError, ValueError):
            state.context["new_entries"] = []

    async def commit(self, state: AgentState) -> None:
        for entry in state.context.get("new_entries", []):
            await self.store.save_world_entry(entry)
