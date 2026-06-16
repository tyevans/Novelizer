from __future__ import annotations
import json
from novelizer.agents.base import BaseAgent, AgentState
from novelizer.store.models import Chapter, DirectorSignal, EditorialStatus, SignalKind
from novelizer.store.queries import Store

SYSTEM_PROMPT = """You are the Editor of a living fictional world's story.
Review the provided chapter for prose quality, narrative coherence, and consistency with context.

Respond with JSON:
  "verdict": "approve" or "revise",
  "notes": string (if revise: specific actionable feedback; if approve: brief praise)

Respond with ONLY the JSON object."""


class Editor(BaseAgent):
    def __init__(self, store: Store, min_interval: int = 120, llm_model: str = "llama3.2") -> None:
        super().__init__(name="editor", store=store, min_interval=min_interval, llm_model=llm_model)

    async def readiness_check(self) -> float:
        drafts = await self.store.db.count_draft_chapters()
        return min(1.0, drafts / 3)

    async def poll(self, state: AgentState) -> None:
        drafts = await self.store.list_chapters(status=EditorialStatus.draft)
        state.context["target_chapter"] = drafts[0] if drafts else None

    async def work(self, state: AgentState) -> None:
        chapter = state.context.get("target_chapter")
        if not chapter:
            state.context["verdict"] = None
            return

        user_msg = f"Chapter title: {chapter.title}\n\nProse:\n{chapter.prose}"
        raw = await self._llm([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ])
        try:
            data = json.loads(raw)
            state.context["verdict"] = data.get("verdict", "approve")
            state.context["notes"] = data.get("notes", "")
        except (json.JSONDecodeError, TypeError):
            state.context["verdict"] = "approve"
            state.context["notes"] = ""

    async def commit(self, state: AgentState) -> None:
        chapter = state.context.get("target_chapter")
        verdict = state.context.get("verdict")
        if not chapter or not verdict:
            return

        if verdict == "approve":
            chapter.editorial_status = EditorialStatus.reviewed
            chapter.editor_notes = state.context.get("notes", "")
            await self.store.save_chapter(chapter)
        else:
            notes = state.context.get("notes", "")
            sig = DirectorSignal(
                kind=SignalKind.note,
                body=f"Editor feedback for '{chapter.title}': {notes}",
                target_agent="author",
            )
            await self.store.save_director_signal(sig)
