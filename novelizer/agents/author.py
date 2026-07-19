from __future__ import annotations
from novelizer.agents.base import BaseAgent, ChapterDraft, Runner
from novelizer.brain.context import causal_flags_note, known_secrets_note, stale_threads_note
from novelizer.brain.staleness import STALENESS_THRESHOLD_CHAPTERS
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationHandConsumed
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.canon.events import ChapterRevised
from novelizer.muse.prompts import AI_TELL_BAN_NOTE, casting_pool_note, inspiration_note
from novelizer.store.models import Chapter, SignalKind

AUTHOR_SYSTEM_PROMPT = """You are the Author of a living fictional world. Write the next prose chapter.
You receive world lore, active characters, previous chapter summaries, and director notes.
Write a self-contained chapter with a clear narrative beat, 2-5 paragraphs.
Return a title, the full prose, and the ids of characters who appear.
""" + AI_TELL_BAN_NOTE


def _summarize(
    ctx: dict,
    casting_note: str = "",
    personality: str = "",
    prior_chapter_chars: int = 200,
    staleness_threshold_chapters: int = STALENESS_THRESHOLD_CHAPTERS,
) -> str:
    world = "\n".join(f"- {e.title}: {e.body[:150]}" for e in ctx["world"][:10]) or "None yet."
    chars = "\n".join(f"- {c.name}: {c.traits} | arc: {c.arc_status}" for c in ctx["characters"][:8]) or "None yet."
    prev = "\n".join(f"- '{c.title}': {c.prose[:prior_chapter_chars]}" for c in ctx["previous"]) or "None yet."
    notes = "\n".join(f"Director: {s.body}" for s in ctx["signals"]) or "None."
    voice = BaseAgent._guarded_line("Write in this prose voice", casting_note)
    cast = BaseAgent._guarded_line("In character", personality)
    brain = stale_threads_note(ctx["threads"], ctx["chapters"], threshold=staleness_threshold_chapters)
    secrets = known_secrets_note(ctx["secrets"], ctx["characters"], ctx["knowledge_matrix"])
    causal = causal_flags_note(ctx["causal_edges"], [c.id for c in ctx["chapters"]])
    pool = casting_pool_note(ctx.get("hand"))
    sparks = inspiration_note(ctx.get("hand"))
    return (
        f"World lore:\n{world}\n\nCharacters:\n{chars}\n\n"
        f"Previous chapters:\n{prev}\n\nDirector notes:\n{notes}{pool}{sparks}{voice}{cast}{brain}{secrets}{causal}\n\nWrite the next chapter."
    )


def _revise_summarize(target: Chapter, revise_signal, casting_note: str = "", personality: str = "") -> str:
    voice = BaseAgent._guarded_line("Write in this prose voice", casting_note)
    cast = BaseAgent._guarded_line("In character", personality)
    return (
        f"You are revising an existing chapter. Rewrite it in full, addressing the "
        f"feedback below.\n\nChapter title: {target.title}\n\nOriginal prose:\n{target.prose}\n\n"
        f"Editor feedback: {revise_signal.body}{voice}{cast}\n\nWrite the revised chapter."
    )


def _find_revise_signal(signals):
    return next((s for s in signals if s.kind == SignalKind.revise), None)


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
        prior_chapter_summary_chars: int = 200,
        staleness_threshold_chapters: int = STALENESS_THRESHOLD_CHAPTERS,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="author", personality=personality)
        self._casting_note = casting_note
        self.provenance = provenance
        self._prior_chapter_summary_chars = prior_chapter_summary_chars
        self._staleness_threshold_chapters = staleness_threshold_chapters

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
            "causal_edges": await self._read.list_causal_edges(),
            "hand": await self._read.get_active_hand(),
        }

    async def work(self, ctx: dict) -> ChapterDraft | None:
        revise_signal = _find_revise_signal(ctx["signals"])
        target = None
        if revise_signal is not None:
            target = next((c for c in ctx["chapters"] if c.id == revise_signal.target_entity), None)
        if revise_signal is not None and target is not None:
            content = _revise_summarize(target, revise_signal, self._casting_note, self.personality)
        else:
            content = _summarize(
                ctx, self._casting_note, self.personality,
                prior_chapter_chars=self._prior_chapter_summary_chars,
                staleness_threshold_chapters=self._staleness_threshold_chapters,
            )
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": content}]})
        return result.get("structured_response")

    async def commit(self, draft: ChapterDraft | None, ctx: dict) -> None:
        if draft is None:
            return
        revise_signal = _find_revise_signal(ctx["signals"])
        target = None
        if revise_signal is not None:
            target = next((c for c in ctx["chapters"] if c.id == revise_signal.target_entity), None)
        if revise_signal is not None and target is not None:
            chapter_id = target.id
            revised = ChapterRevised(chapter_id=chapter_id, prose=draft.prose, editor_notes_ref=revise_signal.id)
            await self._committer.commit(self.name, EventType.CHAPTER_REVISED, chapter_id, revised)
            valid_chapter_ids = {c.id for c in ctx["chapters"]}
        else:
            chapter = Chapter(
                title=draft.title, prose=draft.prose, character_ids=draft.character_ids, provenance=self.provenance
            )
            await self._committer.commit(self.name, EventType.CHAPTER_CREATED, chapter.id, chapter)
            chapter_id = chapter.id
            valid_chapter_ids = {c.id for c in ctx["chapters"]} | {chapter.id}
            hand = ctx.get("hand")
            if hand is not None:
                await self._committer.commit(
                    self.name, EventType.INSPIRATION_HAND_CONSUMED, hand.id,
                    InspirationHandConsumed(hand_id=hand.id, chapter_id=chapter.id),
                )
        active_thread_ids = {
            t.id for t in ctx["threads"] if t.state.value not in TERMINAL_STATES
        }
        await self._commit_thread_intents(draft.thread_intents, active_thread_ids, chapter_id=chapter_id)
        active_theme_ids = {t.id for t in ctx["themes"]}
        await self._commit_theme_intents(draft.theme_intents, active_theme_ids, chapter_id=chapter_id)
        active_secret_ids = {s.id for s in ctx["secrets"]}
        await self._commit_knowledge_intents(draft.knowledge_intents, active_secret_ids, chapter_id=chapter_id)
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
