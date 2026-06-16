from __future__ import annotations
import json
from novelizer.agents.base import BaseAgent, AgentState
from novelizer.store.models import Character, RetconRequest
from novelizer.store.queries import Store

SYSTEM_PROMPT = """You are the Character Keeper for a living fictional world.
You receive a list of characters with their traits and motivations, and recent prose chapters.
Your tasks:
1. Update each character's arc_status based on recent events in chapters.
2. Flag any behavioral contradictions between a character's defined traits and their actions in chapters.

Respond with JSON with two keys:
  "updated_characters": list of character objects (id, name, traits, motivations, backstory, arc_status, aliases, relationships)
  "retcon_requests": list of objects with (description, conflicting_entry_ids, proposed_resolution)

Respond with ONLY the JSON object."""


class CharacterKeeper(BaseAgent):
    def __init__(self, store: Store, min_interval: int = 120, llm_model: str = "llama3.2") -> None:
        super().__init__(name="character_keeper", store=store, min_interval=min_interval, llm_model=llm_model)

    async def readiness_check(self) -> float:
        return 0.5

    async def poll(self, state: AgentState) -> None:
        state.context["characters"] = await self.store.list_characters()
        chapters = await self.store.list_chapters()
        state.context["recent_chapters"] = chapters[-5:]

    async def work(self, state: AgentState) -> None:
        chars = state.context["characters"]
        chapters = state.context["recent_chapters"]
        if not chars:
            state.context["updated_characters"] = []
            state.context["retcon_requests"] = []
            return

        char_summaries = [
            f"- {c.name} (id:{c.id}): traits={c.traits}, arc={c.arc_status}"
            for c in chars
        ]
        chapter_excerpts = [f"Chapter '{ch.title}': {ch.prose[:300]}" for ch in chapters]

        user_msg = (
            "Characters:\n" + "\n".join(char_summaries) +
            "\n\nRecent chapters:\n" + "\n\n".join(chapter_excerpts)
        )
        raw = await self._llm([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ])
        try:
            data = json.loads(raw)
            state.context["updated_characters"] = [Character(**c) for c in data.get("updated_characters", [])]
            state.context["retcon_requests"] = [
                RetconRequest(**r) for r in data.get("retcon_requests", [])
            ]
        except (json.JSONDecodeError, TypeError, ValueError):
            state.context["updated_characters"] = []
            state.context["retcon_requests"] = []

    async def commit(self, state: AgentState) -> None:
        for char in state.context.get("updated_characters", []):
            await self.store.save_character(char)
        for req in state.context.get("retcon_requests", []):
            await self.store.save_retcon_request(req)
