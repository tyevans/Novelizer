from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE, DEFAULT_PASS_REMARK, PASS_PROMPT_INSTRUCTION
from agent_kit import GRAPH_RECURSION_LIMIT
from novelizer.agents.schemas import (
    ContinuityOutput, MinedFactsOutput, MinedInspirationFact, ThreadIntent, KnowledgeIntent, CausalIntent,
    PromiseIntent,
)
from novelizer.brain.context import chapter_map_note, open_retcons_note
from novelizer.brain.context_assembly import AdvisoryEntry, assemble_advisory
from novelizer.brain.leaks import find_leaks, leak_description
from novelizer.brain.paradoxes import find_paradoxes, paradox_description
from novelizer.brain.mining import MINED_SOURCE_TAG, thread_touch_log
from novelizer.brain.watermarks import current_done_ids
from novelizer.canon.promises import TERMINAL_PROMISE_STATES
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, ChapterMined, InspirationUptakeRecorded
from novelizer.store.models import Flag, FlagStatus
from novelizer.text_chunk import chunk_prose

logger = logging.getLogger(__name__)

# Same rationale as kg_projector.py's extraction chunking: a chapter mined
# in one call can produce a structured-output response that overruns
# llm_max_tokens on fact-dense chapters, truncating mid-JSON. Smaller than
# embeddings.py's 6000-char retrieval chunk size because the failure mode
# here is output length, not input length.
_MINING_CHUNK_CHARS = 3000
_MINING_CHUNK_OVERLAP = 200

SYSTEM_PROMPT = """You are the Continuity Checker for a living novel written chapter by chapter,
without stopping. You FIND contradictions in the canon and file each as a retcon_request. You do
not repair them.

## Your lane, and the lanes that are not
You find contradictions; the Retconner fixes them. Never rewrite prose or lore yourself —
proposed_resolution is one line pointing at the fix, not the fix.
Two whole error classes are already caught by code every cycle. Do NOT re-report them:
- Secret leaks: a character using a secret they were never shown learning.
- Causal paradoxes: an effect chapter ordered before its cause.
Spend no attention there. Your value is what code cannot see: contradictions in the actual prose
and in the character and world sheets.

## What to look for
Weight scrutiny toward the two classes that actually break long stories:
- Timeline and plot logic: dates, durations, ages, "three days later", an event that cannot have
  happened in the stated order.
- Factual and detail consistency: a name, an eye colour, a place, a quantity, a relationship
  stated one way here and another way there.
Contradictions cluster in the MIDDLE of a long manuscript and accumulate with length. Read toward
mid-book chapters, not only the newest.

## How to work — research first, then file
The context below is an index and short summaries, NOT the source of truth. Before filing anything,
read the full passage on BOTH sides of the suspected conflict.
Ground every retcon in real quotations: the description MUST quote both conflicting spans and say
where each one is (chapter title or entry id). No quote, no flag — if you cannot cite both sides,
you have not found a contradiction. The moment you can cite both, stop searching and file.

## Output and when to stay silent
Each retcon_request: description (the two quoted spans, their locations, and what conflicts),
conflicting_entry_ids, proposed_resolution (one line). You may be shown retcons already open — do
not re-report those, even reworded.
A pass that files nothing is a SUCCESS, not a wasted turn. Inventing a marginal contradiction to
look busy poisons the Retconner's queue, and it is the failure this role most often commits. The
balance: if you CAN cite both sides of a real contradiction, you must file it — silence on a
genuine conflict is equally a failure.""" + PASS_PROMPT_INSTRUCTION

MINING_SYSTEM_PROMPT = """You are the prose-mining pass of the Continuity Checker. You read ONE
chapter's full prose plus the current knowledge matrix, the active secret and thread ids, the
causal edges, and — if listed — the inspiration items dealt to this chapter. You extract facts the
prose plainly SHOWS on the page that the log has no covering event for. You are an EXTRACTOR, not
a judge: you do not decide whether anything is wrong, only report what the prose depicts.

## What you may report
- secret_facts: a character learns a secret (action="learn") or acts on one they already hold
  (action="uses").
- reveal_facts: a secret is exposed in the open, to a room or a crowd.
- thread_facts: a plot thread is touched, planted into, or paid off.
- causal_facts: an event in one chapter causes an event in another.
- inspiration_facts: a dealt inspiration item the prose visibly uses.

## Cite existing ids only — never invent one
Every id you emit must already appear in the lists you were given; you never mint a new secret or
thread. Keep the namespaces separate: a SECRET id names a hidden fact (e.g. 'the-heir-lives'), and
only ids in the "Active secret ids" list are legal secret ids. Thread ids and character names are
NEVER secret ids.
If a fact clearly fits but its id is not in the given list, report it anyway with known_id=false.
That is the correct, safe move: it routes the fact to review instead of dropping it or forcing a
wrong id.

## learn vs uses — the distinction that matters most
Report "learn" ONLY when the chapter shows the moment of acquisition on the page: the character
overhears it, reads it, is told it, or works it out in the reader's view. Report "uses" when the
character ACTS on knowledge they already hold and no learning moment is shown this chapter. When
unsure, it is "uses" — a shown learning moment is a high bar.
- "Mara pressed the letter flat and read the single line: the heir lives. Her hands went cold."
  -> secret_fact action="learn" (acquisition happens on the page).
- "Kestrel walked straight to the third grave — the one no one had told her held the heir."
  -> secret_fact action="uses" (she acts on it; no learning is shown).
- "'The heir lives!' the herald cried across the square." -> reveal_fact, not learn or uses.

## Promises
If the prompt lists open promises, also report promise_progress_facts: for each open promise whose
expectation this chapter's prose clearly references or develops, report its id (cited exactly,
never invented) with a one-sentence note. Only the listed ids are legal.

## Discipline
Report a fact only if the prose SHOWS it. Never infer an offstage event, and never report a fact
the given matrix, references or edges already cover. Keep every note to ONE short sentence. Return
empty lists when the prose shows nothing new: an empty result is a correct, successful pass, not a
failure. For inspiration_facts, only items from the dealt list are legal — never invent one."""

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
        pull_mode: bool = False,
        advisory_token_budget: int = 2000,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="continuity_checker", personality=personality)
        self._mining_runner = mining_runner
        self._events = event_store
        self.pull_mode = pull_mode
        self._advisory_token_budget = advisory_token_budget

    async def readiness(self) -> float:
        open_retcons = len(await self._read.list_flags(category="contradiction", status=FlagStatus.open))
        return await self._gate_on_watermark(max(0.1, 1.0 - open_retcons / 5))

    async def _fingerprint(self) -> tuple:
        chapters = await self._read.list_chapters()
        mined_events = await self._events.events_since(0, event_types=[EventType.CHAPTER_MINED])
        revised_events = await self._events.events_since(0, event_types=[EventType.CHAPTER_REVISED])
        already_mined = current_done_ids(mined_events, revised_events)
        unmined = sum(1 for c in chapters if c.id not in already_mined)
        refs = await self._read.list_secret_references()
        edges = await self._read.list_causal_edges()
        return (len(chapters), chapters[-1].id if chapters else "", unmined, len(refs), len(edges))

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        mined_events = await self._events.events_since(0, event_types=[EventType.CHAPTER_MINED])
        revised_events = await self._events.events_since(0, event_types=[EventType.CHAPTER_REVISED])
        already_mined = current_done_ids(mined_events, revised_events)
        thread_events = await self._events.events_since(
            0, event_types=[EventType.THREAD_PLANTED, EventType.THREAD_TOUCHED,
                             EventType.THREAD_PAID_OFF, EventType.THREAD_ABANDONED],
        )
        return {
            "open_retcons": await self._read.list_flags(category="contradiction", status=FlagStatus.open),
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "chapters": chapters[-10:],
            "chapter_order": [c.id for c in chapters],
            "secret_references": await self._read.list_secret_references(),
            "knowledge_matrix": await self._read.knowledge_matrix(),
            "causal_edges": await self._read.list_causal_edges(),
            "threads": await self._read.list_threads(),
            "secrets": await self._read.list_secrets(),
            "promises": await self._read.list_promises(),
            "mined_chapters": [c for c in chapters if c.id not in already_mined],
            "thread_touch_pairs": thread_touch_log(thread_events),
            "hands_by_chapter": {
                h.consumed_chapter_id: h
                for h in await self._read.list_hands(status="consumed")
                if h.consumed_chapter_id
            },
            "summaries": await self._read.list_chapter_summaries(),
        }

    async def work(self, ctx: dict) -> tuple[ContinuityOutput | None, dict[str, MinedFactsOutput]]:
        if self.pull_mode:
            # Index only: the prompt requires reading both sides of a conflict
            # before filing, so pushed bodies and traits are the stale summary
            # it must not work from -- ids and names locate, read_file grounds.
            world = "\n".join(f"[{e.id[:8]}] {e.title}" for e in ctx["world"][:20]) or "None."
            chars = "\n".join(f"[{c.id[:8]}] {c.name}" for c in ctx["characters"][:10]) or "None."
        else:
            world = "\n".join(f"[{e.id[:8]}] {e.title}: {e.body[:200]}" for e in ctx["world"][:20]) or "None."
            chars = "\n".join(f"[{c.id[:8]}] {c.name}: {c.traits}" for c in ctx["characters"][:10]) or "None."
        cast = self._guarded_line("In character", self.personality)
        retcons = open_retcons_note(ctx.get("open_retcons", []))
        if self.pull_mode:
            chapters_block = (
                f"Chapter index:\n"
                f"{chapter_map_note(ctx['chapters'], gists={s.chapter_id: s.gist for s in ctx['summaries'] if s.gist})}"
            )
        else:
            summaries_by_id = {s.chapter_id: s.summary for s in ctx["summaries"]}
            entries = [
                AdvisoryEntry(label=f"[{c.id[:8]}] {c.title}", summary=summaries_by_id.get(c.id), verbatim=c.prose)
                for c in ctx["chapters"]
            ]
            chapters = assemble_advisory(entries, self._advisory_token_budget) or "None."
            chapters_block = f"Recent chapters:\n{chapters}"
        msg = f"World entries:\n{world}\n\nCharacters:\n{chars}\n\n{chapters_block}{retcons}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        out = result.get("structured_response")

        mined: dict[str, MinedFactsOutput] = {}
        for chapter in ctx.get("mined_chapters", []):
            chapter_mined = await self._mine_chapter(chapter, ctx)
            if chapter_mined is not None:
                mined[chapter.id] = chapter_mined
        return out, mined

    async def _mine_chapter(self, chapter, ctx: dict) -> MinedFactsOutput | None:
        chunks = chunk_prose(chapter.prose, _MINING_CHUNK_CHARS, _MINING_CHUNK_OVERLAP)
        merged: dict[str, list] = {
            "secret_facts": [], "reveal_facts": [], "thread_facts": [],
            "causal_facts": [], "inspiration_facts": [], "promise_progress_facts": [],
        }
        feed_note = ""
        for chunk in chunks:
            mining_msg = self._mining_prompt(chapter, ctx, chunk)
            try:
                mining_result = await self._mining_runner.ainvoke({"messages": [{"role": "user", "content": mining_msg}]})
            except Exception:
                logger.warning(
                    "%s: mining pass for chapter %r raised an exception; not stamped "
                    "chapter.mined, will retry next poll", self.name, chapter.id, exc_info=True,
                )
                return None
            mining_out = mining_result.get("structured_response")
            if not isinstance(mining_out, MinedFactsOutput):
                logger.warning(
                    "%s: mining pass for chapter %r returned no usable structured response "
                    "(%r); not stamped chapter.mined, will retry next poll",
                    self.name, chapter.id, type(mining_out).__name__,
                )
                return None
            for field in merged:
                merged[field].extend(getattr(mining_out, field))
            if mining_out.feed_note:
                feed_note = mining_out.feed_note
        return MinedFactsOutput(**merged, feed_note=feed_note)

    def _mining_prompt(self, chapter, ctx: dict, prose_chunk: str) -> str:
        # The secret namespace must be stated outright: shown only implicitly
        # (via matrix lines), the live miner cited thread ids and character
        # names as secret ids (a-dress-for-doug, 2026-07-19).
        secret_ids = ", ".join(s.id for s in ctx.get("secrets", [])) or "(none exist yet)"
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
        hand = ctx.get("hands_by_chapter", {}).get(chapter.id)
        dealt = ""
        if hand is not None:
            dealt = (
                f"\n\nDealt inspiration items for this chapter:\n"
                f"professions: {', '.join(hand.professions) or '(none)'}\n"
                f"settings: {', '.join(hand.settings) or '(none)'}\n"
                f"beats: {', '.join(hand.beats) or '(none)'}"
            )
        open_promises = [
            p for p in ctx.get("promises", []) if p.state.value not in TERMINAL_PROMISE_STATES
        ]
        promises_block = ""
        if open_promises:
            promise_lines = "\n".join(f"- {p.id}: {p.name}" for p in open_promises)
            promises_block = f"\n\nOpen promises:\n{promise_lines}"
        return (
            f"Chapter [{chapter.id}] {chapter.title}:\n{prose_chunk}\n\n"
            f"Active secret ids (the ONLY legal values for secret_facts ids; thread ids and "
            f"character names are never secret ids): {secret_ids}\n\n"
            f"Knowledge matrix:\n{matrix}\n\nSecret references:\n{secret_refs}\n\n"
            f"Threads:\n{threads}\n\nCausal edges:\n{causal}{dealt}{promises_block}"
        )

    async def _commit_mined_facts(
        self, chapter_id: str, mined_out: MinedFactsOutput, ctx: dict, seen_descriptions: set[str],
    ) -> None:
        active_secret_ids = {s.id for s in ctx.get("secrets", [])}
        active_thread_ids = {t.id for t in ctx.get("threads", [])}
        matrix = ctx.get("knowledge_matrix", {})
        secret_refs = {(r.secret_id, r.character_id) for r in ctx.get("secret_references", [])}
        thread_touches = ctx.get("thread_touch_pairs", set())
        causal_pairs = {(e.cause_chapter_id, e.effect_chapter_id) for e in ctx.get("causal_edges", [])}
        valid_chapter_ids = set(ctx.get("chapter_order", []))

        for fact in mined_out.secret_facts:
            if not fact.known_id or fact.id not in active_secret_ids:
                if fact.id in active_thread_ids:
                    # Namespace confusion, not a new fact: the cited "secret" is
                    # an active thread (live: 'the-boy-s-gift', 'the-name-of-the-sea').
                    # The recoverable meaning — the prose engages that thread — is
                    # a mined touch, same downgrade precedent as plant-collision
                    # → touch. Never a retcon: the Retconner can't act on it.
                    if (fact.id, chapter_id) in thread_touches:
                        logger.info(
                            "%s: mined secret %s fact cites active thread id %r already touched "
                            "in chapter %r, skipped", self.name, fact.action, fact.id, chapter_id,
                        )
                        continue
                    logger.info(
                        "%s: mined secret %s fact cites active thread id %r in chapter %r, "
                        "redirected to a mined thread touch", self.name, fact.action, fact.id, chapter_id,
                    )
                    await self._commit_thread_intents(
                        [ThreadIntent(action="touch", id=fact.id, note=fact.note)],
                        active_thread_ids, chapter_id=chapter_id, source="mined",
                    )
                    continue
                logger.warning(
                    "%s: mined secret %s fact citing unrecognized/unknown secret id %r for "
                    "character %r in chapter %r escalated to retcon",
                    self.name, fact.action, fact.id, fact.character_id, chapter_id,
                )
                await self._file_mined_flag(
                    f"mined secret {fact.action} fact citing unrecognized/unknown secret id "
                    f"'{fact.id}' for character '{fact.character_id}' in chapter '{chapter_id}'",
                    [fact.id, fact.character_id, chapter_id],
                    seen_descriptions,
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
            await self._file_mined_flag(
                f"mined secret reveal fact citing id '{fact.id}' in chapter '{chapter_id}'",
                [fact.id, chapter_id],
                seen_descriptions,
            )

        for fact in mined_out.thread_facts:
            if not fact.known_id or fact.id not in active_thread_ids:
                logger.warning(
                    "%s: mined thread %s fact citing unrecognized/unknown thread id %r in "
                    "chapter %r escalated to retcon", self.name, fact.action, fact.id, chapter_id,
                )
                await self._file_mined_flag(
                    f"mined thread {fact.action} fact citing unrecognized/unknown thread id "
                    f"'{fact.id}' in chapter '{chapter_id}'",
                    [fact.id, chapter_id],
                    seen_descriptions,
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

        active_promise_ids = {
            p.id for p in ctx.get("promises", []) if p.state.value not in TERMINAL_PROMISE_STATES
        }
        if mined_out.promise_progress_facts:
            await self._commit_promise_intents(
                [
                    PromiseIntent(action="progress", id=fact.promise_id, note=fact.note)
                    for fact in mined_out.promise_progress_facts
                ],
                active_promise_ids, active_thread_ids, chapter_id=chapter_id, source="mined",
            )

        hand = ctx.get("hands_by_chapter", {}).get(chapter_id)
        for fact in mined_out.inspiration_facts:
            if hand is None:
                logger.info(
                    "%s: mined inspiration fact %r for chapter %r with no consumed hand, dropped",
                    self.name, fact.item, chapter_id,
                )
                continue
            dealt_pool = {"professions": hand.professions, "settings": hand.settings,
                          "beats": hand.beats}[fact.kind]
            match = next((d for d in dealt_pool if d.lower() == fact.item.strip().lower()), None)
            if match is None:
                logger.info(
                    "%s: mined inspiration fact %r not in the dealt %s for chapter %r, dropped",
                    self.name, fact.item, fact.kind, chapter_id,
                )
                continue
            await self._committer.commit(
                self.name, EventType.INSPIRATION_UPTAKE_RECORDED, hand.id,
                InspirationUptakeRecorded(hand_id=hand.id, kind=fact.kind, item=match,
                                          chapter_id=chapter_id),
            )

        await self._committer.commit(
            self.name, EventType.CHAPTER_MINED, chapter_id, ChapterMined(chapter_id=chapter_id)
        )

    async def _file_mined_flag(
        self, detail: str, conflicting_entry_ids: list[str], seen_descriptions: set[str],
    ) -> None:
        """File a mined-fact escalation flag, deduped by description against
        `seen_descriptions` — the open queue plus everything filed this cycle.
        The live miner emitted the same fact twice in one output and both were
        filed; a crash before the chapter.mined stamp re-files on the next pass.
        """
        description = f"{MINED_SOURCE_TAG} {detail}"
        if description in seen_descriptions:
            logger.info("%s: skipped duplicate mined flag %r", self.name, description)
            return
        seen_descriptions.add(description)
        flag = Flag(
            category="contradiction",
            filed_by=self.name,
            description=description,
            related_entry_ids=conflicting_entry_ids,
            proposed_resolution="Review the mined fact and add a covering event, or dismiss if not applicable.",
        )
        await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)

    async def commit(
        self, out: ContinuityOutput | None, ctx: dict, mined_facts: dict[str, MinedFactsOutput] | None = None,
    ) -> None:
        open_reqs = await self._read.list_flags(category="contradiction", status=FlagStatus.open)
        seen_descriptions = {r.description for r in open_reqs}
        deterministic_filed = 0

        if out is not None and not out.no_action:
            for r in out.flags:
                if r.description in seen_descriptions:
                    continue
                seen_descriptions.add(r.description)
                flag = Flag(category=r.category, filed_by=self.name, description=r.description,
                            related_entry_ids=r.related_entry_ids, proposed_resolution=r.proposed_resolution)
                await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
            await self._remark(out.feed_note)

        for leak in find_leaks(ctx.get("secret_references", []), ctx.get("knowledge_matrix", {})):
            description = leak_description(leak)
            if description in seen_descriptions:
                continue
            seen_descriptions.add(description)
            flag = Flag(
                category="contradiction",
                filed_by=self.name,
                description=description,
                related_entry_ids=[leak.secret_id, leak.character_id, leak.chapter_id],
                proposed_resolution="Review whether the reference should be removed or a learn/reveal event added.",
            )
            await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
            deterministic_filed += 1

        for paradox in find_paradoxes(ctx.get("causal_edges", []), ctx.get("chapter_order", [])):
            description = paradox_description(paradox)
            if description in seen_descriptions:
                continue
            seen_descriptions.add(description)
            flag = Flag(
                category="contradiction",
                filed_by=self.name,
                description=description,
                related_entry_ids=[paradox.cause_chapter_id, paradox.effect_chapter_id],
                proposed_resolution="Review the causal edge for an ordering or cycle correction.",
            )
            await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
            deterministic_filed += 1

        for chapter_id, mined_out in (mined_facts or {}).items():
            await self._commit_mined_facts(chapter_id, mined_out, ctx, seen_descriptions)

        if out is not None and out.no_action:
            await self._remark(out.feed_note or DEFAULT_PASS_REMARK)
            if not mined_facts and deterministic_filed == 0:
                self.note_pass()

    async def _run(self) -> None:
        fp_seen = await self._fingerprint()
        ctx = await self.poll()
        out, mined = await self.work(ctx)
        await self.commit(out, ctx, mined)
        fp_now = await self._fingerprint()
        # unmined > 0 at record time means a mining pass failed (its "will
        # retry next poll" contract requires the gate stay open) or new prose
        # arrived mid-run; moved chapter components likewise mean this run
        # never saw the newest chapter. Either way, leave the watermark clear.
        # Own stamps/refs/edges are absorbed via fp_now otherwise.
        if fp_now[2] == 0 and fp_now[:2] == fp_seen[:2]:
            self._last_fingerprint = fp_now
        else:
            self._clear_watermark()


def build_continuity_checker_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
    from deepagents import create_deep_agent
    from novelizer.agents.author import RETRIEVAL_NOTE
    from agent_kit import build_chat_model
    from agent_kit import ExcludeToolsMiddleware
    # See build_author_runner: tool executions run under invoke-time graph
    # config, not constructor callbacks on the model, so telemetry callbacks
    # are bound graph-scope via with_config below.
    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        settings.agent_temperature, max_tokens=settings.llm_max_tokens,
        callbacks=None, streaming=callbacks is not None,
    )
    if backend is not None:
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE + OUTPUT_CONVENTIONS_NOTE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=ContinuityOutput,
            backend=backend, tools=tools, subagents=subagents,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    graph = create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=ContinuityOutput)
    if callbacks:
        return graph.with_config({"callbacks": callbacks})
    return graph


def build_continuity_mining_runner(settings, callbacks=None):
    from deepagents import create_deep_agent
    from langchain.agents.structured_output import ProviderStrategy
    from agent_kit import build_chat_model
    # Mining is fact extraction, not composition: run cold regardless of the
    # room's creative temperature. At 0.8 the model free-runs inside JSON
    # string fields until the token cap (observed live: LengthFinishReasonError
    # at completion_tokens=4096).
    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        temperature=0.2, max_tokens=settings.llm_max_tokens, callbacks=callbacks,
    )
    # ProviderStrategy pushes the schema to the endpoint as OpenAI json_schema
    # response_format; llama.cpp grammar-constrains decoding, so local models
    # cannot emit fenced-JSON text instead of the structured channel (observed
    # live with the default tool-calling strategy: correct facts, None
    # structured_response). Mining is single-shot with no tools, so
    # constraining generation is safe here.
    return create_deep_agent(model=model, system_prompt=MINING_SYSTEM_PROMPT, response_format=ProviderStrategy(MinedFactsOutput))


from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant, SubagentGrant


def _construct(ctx: AgentContext) -> ContinuityChecker:
    enabled = ctx.settings.checker_tools_enabled
    subagent_enabled = ctx.settings.checker_subagent_enabled
    builder = ctx.tooled(build_continuity_checker_runner, enabled, subagent_enabled, "continuity_checker")
    runner = ctx.runner_for("continuity_checker", builder)
    mining_runner = ctx.runner_for(
        "continuity_checker_mining", build_continuity_mining_runner,
        fallback_name="continuity_checker",
    )
    return ContinuityChecker(
        runner, mining_runner, ctx.read, ctx.committer, ctx.events,
        interval=ctx.settings.continuity_interval,
        personality=ctx.personalities.get("continuity_checker", ""),
        pull_mode=enabled,
        advisory_token_budget=ctx.settings.advisory_token_budget,
    )


SPEC = AgentSpec(
    name="continuity_checker",
    tool_grant=ToolGrant(enabled_setting="checker_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="checker_subagent_enabled"),
    construct=_construct,
)
