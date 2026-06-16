from __future__ import annotations
import json
from novelizer.agents.base import BaseAgent, AgentState
from novelizer.store.models import Chapter
from novelizer.store.queries import Store

SYSTEM_PROMPT = """You are the Author of a living fictional world. Write the next prose chapter.
You receive: world lore, active characters, previous chapter summaries, and director notes.
Write a self-contained chapter with a clear narrative beat. 2-5 paragraphs.

Respond with JSON with keys:
  "title": string (chapter title),
  "prose": string (the full prose),
  "character_ids": list of character ids appearing in the chapter

Respond with ONLY the JSON object."""


class Author(BaseAgent):
    def __init__(self, store: Store, min_interval: int = 300, llm_model: str = "llama3.2") -> None:
        super().__init__(name="author", store=store, min_interval=min_interval, llm_model=llm_model)

    async def readiness_check(self) -> float:
        drafts = await self.store.db.count_draft_chapters()
        return max(0.0, 1.0 - (drafts / 3))

    async def poll(self, state: AgentState) -> None:
        state.context["world_entries"] = await self.store.list_world_entries()
        state.context["characters"] = await self.store.list_characters()
        chapters = await self.store.list_chapters()
        state.context["previous_chapters"] = chapters[-3:]
        signals = await self.store.list_unconsumed_signals(target_agent=self.name)
        broadcast = await self.store.list_unconsumed_signals(target_agent=None)
        state.context["signals"] = signals + [s for s in broadcast if s not in signals]

    async def work(self, state: AgentState) -> None:
        world = state.context["world_entries"]
        chars = state.context["characters"]
        prev = state.context["previous_chapters"]
        signals = state.context["signals"]

        world_summary = "\n".join(f"- {e.title}: {e.body[:150]}" for e in world[:10])
        char_summary = "\n".join(f"- {c.name}: {c.traits} | arc: {c.arc_status}" for c in chars[:8])
        prev_summary = "\n".join(f"- '{ch.title}': {ch.prose[:200]}" for ch in prev)
        director_notes = "\n".join(f"Director: {s.body}" for s in signals)

        user_msg = (
            f"World lore:\n{world_summary or 'None yet.'}\n\n"
            f"Characters:\n{char_summary or 'None yet.'}\n\n"
            f"Previous chapters:\n{prev_summary or 'None yet.'}\n\n"
            f"Director notes:\n{director_notes or 'None.'}\n\n"
            "Write the next chapter."
        )
        raw = await self._llm([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ])
        try:
            data = json.loads(raw)
            state.context["new_chapter"] = Chapter(
                title=data["title"],
                prose=data["prose"],
                character_ids=data.get("character_ids", []),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            state.context["new_chapter"] = None

        for sig in signals:
            await self.store.consume_signal(sig.id)

    async def commit(self, state: AgentState) -> None:
        chapter = state.context.get("new_chapter")
        if chapter:
            await self.store.save_chapter(chapter)
