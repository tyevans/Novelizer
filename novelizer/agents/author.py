from __future__ import annotations
from novelizer.agents.base import BaseAgent, ChapterDraft, Runner
from novelizer.brain.context import known_secrets_note, stale_threads_note
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import Chapter

AUTHOR_SYSTEM_PROMPT = """You are the Author of a living fictional world. Write the next prose chapter.
You receive world lore, active characters, previous chapter summaries, and director notes.
Write a self-contained chapter with a clear narrative beat, 2-5 paragraphs.
Return a title, the full prose, and the ids of characters who appear."""


def _summarize(ctx: dict, casting_note: str = "", personality: str = "") -> str:
    world = "\n".join(f"- {e.title}: {e.body[:150]}" for e in ctx["world"][:10]) or "None yet."
    chars = "\n".join(f"- {c.name}: {c.traits} | arc: {c.arc_status}" for c in ctx["characters"][:8]) or "None yet."
    prev = "\n".join(f"- '{c.title}': {c.prose[:200]}" for c in ctx["previous"]) or "None yet."
    notes = "\n".join(f"Director: {s.body}" for s in ctx["signals"]) or "None."
    voice = f"\n\nWrite in this prose voice: {casting_note}" if casting_note else ""
    cast = f"\n\nIn character: {personality}" if personality else ""
    brain = stale_threads_note(ctx["threads"], ctx["chapters"])
    secrets = known_secrets_note(ctx["secrets"], ctx["characters"], ctx["knowledge_matrix"])
    return (
        f"World lore:\n{world}\n\nCharacters:\n{chars}\n\n"
        f"Previous chapters:\n{prev}\n\nDirector notes:\n{notes}{voice}{cast}{brain}{secrets}\n\nWrite the next chapter."
    )


class Author(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 300,
        casting_note: str = "",
        personality: str = "",
        provenance: dict | None = None,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="author", personality=personality)
        self._casting_note = casting_note
        self.provenance = provenance

    async def readiness(self) -> float:
        drafts = len(await self._read.list_chapters(status="draft"))
        return max(0.0, 1.0 - drafts / 3)

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "previous": chapters[-3:],
            "chapters": chapters,
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
            "threads": await self._read.list_threads(),
            "secrets": await self._read.list_secrets(),
            "knowledge_matrix": await self._read.knowledge_matrix(),
            "themes": await self._read.list_themes(),
        }

    async def work(self, ctx: dict) -> ChapterDraft | None:
        content = _summarize(ctx, self._casting_note, self.personality)
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": content}]})
        return result.get("structured_response")

    async def commit(self, draft: ChapterDraft | None, ctx: dict) -> None:
        if draft is None:
            return
        chapter = Chapter(
            title=draft.title, prose=draft.prose, character_ids=draft.character_ids, provenance=self.provenance
        )
        await self._committer.commit(self.name, EventType.CHAPTER_CREATED, chapter.id, chapter)
        active_thread_ids = {
            t.id for t in ctx["threads"] if t.state.value not in TERMINAL_STATES
        }
        await self._commit_thread_intents(draft.thread_intents, active_thread_ids, chapter_id=chapter.id)
        active_theme_ids = {t.id for t in ctx["themes"]}
        await self._commit_theme_intents(draft.theme_intents, active_theme_ids, chapter_id=chapter.id)
        active_secret_ids = {s.id for s in ctx["secrets"]}
        await self._commit_knowledge_intents(draft.knowledge_intents, active_secret_ids, chapter_id=chapter.id)
        valid_chapter_ids = {c.id for c in ctx["chapters"]} | {chapter.id}
        await self._commit_causal_intents(draft.causal_intents, valid_chapter_ids)
        await self._remark(draft.feed_note)
        await self._consume_signals(ctx["signals"])

    async def run_once(self) -> None:
        ctx = await self.poll()
        draft = await self.work(ctx)
        await self.commit(draft, ctx)


def build_author_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.author_model, settings.llm_base_url, settings.llm_api_key, settings.author_temperature, max_tokens=settings.llm_max_tokens)
    return create_deep_agent(model=model, system_prompt=AUTHOR_SYSTEM_PROMPT, response_format=ChapterDraft)
