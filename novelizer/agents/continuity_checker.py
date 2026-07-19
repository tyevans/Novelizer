from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import (
    ContinuityOutput, MinedFactsOutput, ThreadIntent, KnowledgeIntent, CausalIntent,
)
from novelizer.brain.leaks import find_leaks, leak_description
from novelizer.brain.paradoxes import find_paradoxes, paradox_description
from novelizer.brain.mining import MINED_SOURCE_TAG, already_mined_chapter_ids, thread_touch_log
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, ChapterMined
from novelizer.store.models import RetconRequest, RetconStatus

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Continuity Checker for a living fictional world. Review the given world
entries, characters, and chapter excerpts for contradictions, anachronisms, or logical inconsistencies.
Return retcon_requests, each with a description (what contradicts what), conflicting_entry_ids (the ids
of the conflicting records), and a proposed_resolution. Return an empty list if you find nothing."""

MINING_SYSTEM_PROMPT = """You are the prose-mining pass of the Continuity Checker for a living fictional
world. Read one chapter's prose plus the current knowledge matrix, active secret/thread ids, and causal
edges. Report facts the prose SHOWS but the log has no covering event for: a character using or learning
a secret, a secret being revealed, a thread being touched or paid off, or a causal link between chapters.
Cite existing ids only -- set known_id=False if you cannot confidently match the fact to an existing
secret/thread id. A character ACTING on a secret the prose never showed them learning is a 'uses' fact,
not 'learn' -- report 'learn' only when the chapter shows the moment of learning on the page. Keep every
note to one short sentence. Return empty lists if the prose shows nothing new."""

# Thread actions mined prose can report ("touch", "planted", "paid_off" -- see
# MinedThreadFact) map onto ThreadIntent's authoring vocabulary ("touch",
# "plant", "pay_off", "abandon"). Mining never mints a new thread id (Locked
# decision 3), so a mined "planted" fact for an already-known active id reads
# as "this thread is live again" -- a touch, not a plant.
_MINED_THREAD_ACTION_TO_INTENT = {"touch": "touch", "planted": "touch", "paid_off": "pay_off"}


class ContinuityChecker(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        mining_runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        event_store: EventStore,
        interval: int = 900,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="continuity_checker", personality=personality)
        self._mining_runner = mining_runner
        self._events = event_store

    async def readiness(self) -> float:
        open_retcons = len(await self._read.list_retcon_requests(status=RetconStatus.open))
        return max(0.1, 1.0 - open_retcons / 5)

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        mined_events = await self._events.events_since(0, event_types=[EventType.CHAPTER_MINED])
        already_mined = already_mined_chapter_ids(mined_events)
        thread_events = await self._events.events_since(
            0, event_types=[EventType.THREAD_PLANTED, EventType.THREAD_TOUCHED,
                             EventType.THREAD_PAID_OFF, EventType.THREAD_ABANDONED],
        )
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "chapters": chapters[-10:],
            "chapter_order": [c.id for c in chapters],
            "secret_references": await self._read.list_secret_references(),
            "knowledge_matrix": await self._read.knowledge_matrix(),
            "causal_edges": await self._read.list_causal_edges(),
            "threads": await self._read.list_threads(),
            "secrets": await self._read.list_secrets(),
            "mined_chapters": [c for c in chapters if c.id not in already_mined],
            "thread_touch_pairs": thread_touch_log(thread_events),
        }

    async def work(self, ctx: dict) -> tuple[ContinuityOutput | None, dict[str, MinedFactsOutput]]:
        world = "\n".join(f"[{e.id[:8]}] {e.title}: {e.body[:200]}" for e in ctx["world"][:20]) or "None."
        chars = "\n".join(f"[{c.id[:8]}] {c.name}: {c.traits}" for c in ctx["characters"][:10]) or "None."
        chapters = "\n".join(f"[{c.id[:8]}] {c.title}: {c.prose[:300]}" for c in ctx["chapters"]) or "None."
        cast = f"\n\nIn character: {self.personality}" if self.personality else ""
        msg = f"World entries:\n{world}\n\nCharacters:\n{chars}\n\nRecent chapters:\n{chapters}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        out = result.get("structured_response")

        mined: dict[str, MinedFactsOutput] = {}
        for chapter in ctx.get("mined_chapters", []):
            mining_msg = self._mining_prompt(chapter, ctx)
            try:
                mining_result = await self._mining_runner.ainvoke({"messages": [{"role": "user", "content": mining_msg}]})
            except Exception:
                logger.warning(
                    "%s: mining pass for chapter %r raised an exception; not stamped "
                    "chapter.mined, will retry next poll", self.name, chapter.id, exc_info=True,
                )
                continue
            mining_out = mining_result.get("structured_response")
            if not isinstance(mining_out, MinedFactsOutput):
                logger.warning(
                    "%s: mining pass for chapter %r returned no usable structured response "
                    "(%r); not stamped chapter.mined, will retry next poll",
                    self.name, chapter.id, type(mining_out).__name__,
                )
                continue
            mined[chapter.id] = mining_out
        return out, mined

    def _mining_prompt(self, chapter, ctx: dict) -> str:
        matrix = "\n".join(
            f"[{sid}] revealed={cell['revealed']} known_by={sorted(cell['known_by'])}"
            for sid, cell in ctx.get("knowledge_matrix", {}).items()
        ) or "None."
        secret_refs = "\n".join(
            f"{r.secret_id} used by {r.character_id} in {r.chapter_id}" for r in ctx.get("secret_references", [])
        ) or "None."
        threads = "\n".join(f"[{t.id}] {t.name} ({t.state})" for t in ctx.get("threads", [])) or "None."
        causal = "\n".join(
            f"{e.cause_chapter_id} -> {e.effect_chapter_id}" for e in ctx.get("causal_edges", [])
        ) or "None."
        return (
            f"Chapter [{chapter.id}] {chapter.title}:\n{chapter.prose}\n\n"
            f"Knowledge matrix:\n{matrix}\n\nSecret references:\n{secret_refs}\n\n"
            f"Threads:\n{threads}\n\nCausal edges:\n{causal}"
        )

    async def _commit_mined_facts(self, chapter_id: str, mined_out: MinedFactsOutput, ctx: dict) -> None:
        active_secret_ids = {s.id for s in ctx.get("secrets", [])}
        active_thread_ids = {t.id for t in ctx.get("threads", [])}
        matrix = ctx.get("knowledge_matrix", {})
        secret_refs = {(r.secret_id, r.character_id) for r in ctx.get("secret_references", [])}
        thread_touches = ctx.get("thread_touch_pairs", set())
        causal_pairs = {(e.cause_chapter_id, e.effect_chapter_id) for e in ctx.get("causal_edges", [])}
        valid_chapter_ids = set(ctx.get("chapter_order", []))

        for fact in mined_out.secret_facts:
            if not fact.known_id or fact.id not in active_secret_ids:
                logger.warning(
                    "%s: mined secret %s fact citing unrecognized/unknown secret id %r for "
                    "character %r in chapter %r escalated to retcon",
                    self.name, fact.action, fact.id, fact.character_id, chapter_id,
                )
                await self._file_mined_retcon(
                    f"mined secret {fact.action} fact citing unrecognized/unknown secret id "
                    f"'{fact.id}' for character '{fact.character_id}' in chapter '{chapter_id}'",
                    [fact.id, fact.character_id, chapter_id],
                )
                continue
            if fact.action == "uses" and (fact.id, fact.character_id) in secret_refs:
                logger.info(
                    "%s: skipped mined secret uses fact for %r/%r in chapter %r, already covered",
                    self.name, fact.id, fact.character_id, chapter_id,
                )
                continue
            if fact.action == "learn" and fact.character_id in matrix.get(fact.id, {}).get("known_by", set()):
                logger.info(
                    "%s: skipped mined secret learn fact for %r/%r in chapter %r, already known",
                    self.name, fact.id, fact.character_id, chapter_id,
                )
                continue
            await self._commit_knowledge_intents(
                [KnowledgeIntent(action=fact.action, id=fact.id, character_id=fact.character_id, note=fact.note)],
                active_secret_ids, chapter_id=chapter_id, allowed_actions=frozenset({"learn", "uses"}),
                source="mined",
            )

        for fact in mined_out.reveal_facts:
            logger.info(
                "%s: mined secret reveal fact for %r in chapter %r escalated to retcon, never auto-committed",
                self.name, fact.id, chapter_id,
            )
            await self._file_mined_retcon(
                f"mined secret reveal fact citing id '{fact.id}' in chapter '{chapter_id}'",
                [fact.id, chapter_id],
            )

        for fact in mined_out.thread_facts:
            if not fact.known_id or fact.id not in active_thread_ids:
                logger.warning(
                    "%s: mined thread %s fact citing unrecognized/unknown thread id %r in "
                    "chapter %r escalated to retcon", self.name, fact.action, fact.id, chapter_id,
                )
                await self._file_mined_retcon(
                    f"mined thread {fact.action} fact citing unrecognized/unknown thread id "
                    f"'{fact.id}' in chapter '{chapter_id}'",
                    [fact.id, chapter_id],
                )
                continue
            if (fact.id, chapter_id) in thread_touches:
                logger.info(
                    "%s: skipped mined thread %s fact for %r in chapter %r, already touched",
                    self.name, fact.action, fact.id, chapter_id,
                )
                continue
            intent_action = _MINED_THREAD_ACTION_TO_INTENT[fact.action]
            await self._commit_thread_intents(
                [ThreadIntent(action=intent_action, id=fact.id, note=fact.note)],
                active_thread_ids, chapter_id=chapter_id, source="mined",
            )

        for fact in mined_out.causal_facts:
            if (fact.cause_chapter_id, fact.effect_chapter_id) in causal_pairs:
                continue
            await self._commit_causal_intents(
                [CausalIntent(cause_chapter_id=fact.cause_chapter_id, effect_chapter_id=fact.effect_chapter_id, note=fact.note)],
                valid_chapter_ids, source="mined",
            )

        await self._committer.commit(
            self.name, EventType.CHAPTER_MINED, chapter_id, ChapterMined(chapter_id=chapter_id)
        )

    async def _file_mined_retcon(self, detail: str, conflicting_entry_ids: list[str]) -> None:
        req = RetconRequest(
            description=f"{MINED_SOURCE_TAG} {detail}",
            conflicting_entry_ids=conflicting_entry_ids,
            proposed_resolution="Review the mined fact and add a covering event, or dismiss if not applicable.",
        )
        await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)

    async def commit(
        self, out: ContinuityOutput | None, ctx: dict, mined_facts: dict[str, MinedFactsOutput] | None = None,
    ) -> None:
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

        for chapter_id, mined_out in (mined_facts or {}).items():
            await self._commit_mined_facts(chapter_id, mined_out, ctx)

    async def _run(self) -> None:
        ctx = await self.poll()
        out, mined = await self.work(ctx)
        await self.commit(out, ctx, mined)


def build_continuity_checker_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=ContinuityOutput)


def build_continuity_mining_runner(settings):
    from deepagents import create_deep_agent
    from langchain.agents.structured_output import ProviderStrategy
    from novelizer.agents.llm import build_chat_model
    # Mining is fact extraction, not composition: run cold regardless of the
    # room's creative temperature. At 0.8 the model free-runs inside JSON
    # string fields until the token cap (observed live: LengthFinishReasonError
    # at completion_tokens=4096).
    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        temperature=0.2, max_tokens=settings.llm_max_tokens,
    )
    # ProviderStrategy pushes the schema to the endpoint as OpenAI json_schema
    # response_format; llama.cpp grammar-constrains decoding, so local models
    # cannot emit fenced-JSON text instead of the structured channel (observed
    # live with the default tool-calling strategy: correct facts, None
    # structured_response). Mining is single-shot with no tools, so
    # constraining generation is safe here.
    return create_deep_agent(model=model, system_prompt=MINING_SYSTEM_PROMPT, response_format=ProviderStrategy(MinedFactsOutput))
