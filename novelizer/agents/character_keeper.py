from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner, DEFAULT_PASS_REMARK, PASS_PROMPT_INSTRUCTION, GRAPH_RECURSION_LIMIT
from novelizer.agents.schemas import KeeperOutput
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.brain.context import open_retcons_note
from novelizer.canon.characters import slugify_character_name
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationUptakeRecorded
from novelizer.store.models import Character, RetconRequest, RetconStatus
from novelizer.muse.prompts import NAME_UPTAKE_HAND_WINDOW, name_uptake_matches

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Character Keeper for a living fictional world.
You receive the current cast (with traits and arcs) and recent prose chapters. Your tasks:
1. Report new_characters: named characters who appear in the chapters but are missing from
   the cast. Give each a name exactly as the prose spells it, plus any traits, motivations,
   backstory, arc_status, and voice the prose shows. Never re-report a character already
   in the cast, even under a nickname or variant spelling.
2. Update each existing character's arc_status to reflect what recent chapters show.
3. Flag behavioral contradictions between a character's defined traits and their actions.
4. Note each character's voice: dialogue patterns, vocabulary, and verbal tics you observe
   in their lines, and revise it as their voice evolves across chapters.
5. Declare or advance each significant character's planned arc: the lie they believe, what
   they want vs need, their arc type; plan pivots on blueprint beats; resolve the arc when
   the story settles it. Cite arc and beat ids exactly.
Return new_characters, updated_characters (id + revised arc_status, and any corrected
traits/motivations/backstory/voice), and retcon_requests (description, conflicting_entry_ids,
proposed_resolution). You may also be shown retcon requests already filed and still open:
do not re-report those issues, even reworded.""" + PASS_PROMPT_INSTRUCTION


class CharacterKeeper(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
        prose_chars: int = 6000,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="character_keeper", personality=personality)
        # Discovery needs the whole chapter: a character introduced in the
        # final scene is as canonical as one in the opening line. The cap
        # only bounds tokens for outlier-length chapters.
        self._prose_chars = prose_chars

    async def readiness(self) -> float:
        chars = await self._read.list_characters()
        chapters = await self._read.list_chapters()
        if chapters and not chars:
            # Prose exists but the cast is empty: bootstrapping the cast is
            # the Keeper's most urgent work — nothing else mints characters.
            return 0.8
        score = 0.5 if (chars and chapters) else 0.2
        return await self._gate_on_watermark(score)

    async def _fingerprint(self) -> tuple:
        chapters = await self._read.list_chapters()
        open_retcons = await self._read.list_retcon_requests(status=RetconStatus.open)
        return (len(chapters), chapters[-1].id if chapters else "", len(open_retcons))

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "characters": await self._read.list_characters(),
            "recent": chapters[-5:],
            "secrets": await self._read.list_secrets(),
            "open_retcons": await self._read.list_retcon_requests(status=RetconStatus.open),
            "hands": (await self._read.list_hands(status="consumed"))[-NAME_UPTAKE_HAND_WINDOW:],
            "arcs": await self._read.list_arcs(active_only=True),
            "beats": await self._read.list_beats(),
        }

    async def work(self, ctx: dict) -> KeeperOutput | None:
        if not ctx["characters"] and not ctx["recent"]:
            return None
        chars = "\n".join(f"- {c.name} (id:{c.id}): traits={c.traits}, arc={c.arc_status}" for c in ctx["characters"]) or "None yet."
        chapters = "\n\n".join(f"Chapter '{c.title}': {c.prose[:self._prose_chars]}" for c in ctx["recent"]) or "None."
        cast = self._guarded_line("In character", self.personality)
        retcons = open_retcons_note(ctx.get("open_retcons", []))
        names_by_id = {c.id: c.name for c in ctx["characters"]}
        arcs_lines = "\n".join(
            f"- {names_by_id.get(arc.character_id, arc.character_id)}: {arc.arc_type} arc "
            f"(id:{arc.id}) lie='{arc.lie}' advances={arc.advance_count}"
            for arc in ctx.get("arcs", [])
        )
        beats = ctx.get("beats", [])
        if beats:
            arcs_lines += f"\nAvailable beat ids for pivots: {', '.join(b.id for b in beats)}"
        arcs_block = f"\n\nActive arcs:\n{arcs_lines}" if arcs_lines else ""
        msg = f"Characters:\n{chars}\n\nRecent chapters:\n{chapters}{retcons}{cast}{arcs_block}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: KeeperOutput | None, ctx: dict) -> None:
        if out is None:
            return
        if out.no_action:
            await self._remark(out.feed_note or DEFAULT_PASS_REMARK)
            self.note_pass()
            return
        # Re-read the cast at commit time (not from ctx): the LLM call in
        # work() is slow enough that a concurrent cycle may have minted
        # a character meanwhile; a slug is minted exactly once.
        seen_ids = {c.id for c in await self._read.list_characters()}
        for new in out.new_characters:
            if not new.name.strip():
                logger.warning("%s: dropped new character with empty name", self.name)
                continue
            char_id = slugify_character_name(new.name)
            if char_id in seen_ids:
                logger.info(
                    "%s: new character %r collides with existing id %r, dropped",
                    self.name, new.name, char_id,
                )
                continue
            seen_ids.add(char_id)
            character = Character(
                id=char_id, name=new.name.strip(), traits=new.traits, motivations=new.motivations,
                backstory=new.backstory, arc_status=new.arc_status, voice=new.voice,
            )
            await self._committer.commit(self.name, EventType.CHARACTER_CREATED, char_id, character)
            match = name_uptake_matches(new.name, ctx.get("hands", []))
            if match is not None:
                hand_id, dealt_item = match
                hand = next(h for h in ctx["hands"] if h.id == hand_id)
                await self._committer.commit(
                    self.name, EventType.INSPIRATION_UPTAKE_RECORDED, hand_id,
                    InspirationUptakeRecorded(hand_id=hand_id, kind="names", item=dealt_item,
                                              chapter_id=hand.consumed_chapter_id),
                )
        for upd in out.updated_characters:
            current = await self._read.get_character(upd.id)
            if current is None:
                continue
            fields = {}
            for f in ("arc_status", "traits", "motivations", "backstory", "voice"):
                v = getattr(upd, f)
                if v is not None:
                    fields[f] = v
            updated = current.model_copy(update=fields)
            await self._committer.commit(self.name, EventType.CHARACTER_UPDATED, updated.id, updated)
        if out.retcon_requests:
            # Re-read the queue at commit time (not from ctx): the LLM call in
            # work() is slow enough that another agent may have filed meanwhile.
            open_reqs = await self._read.list_retcon_requests(status=RetconStatus.open)
            seen_descriptions = {r.description for r in open_reqs}
            for r in out.retcon_requests:
                if r.description in seen_descriptions:
                    continue
                seen_descriptions.add(r.description)
                req = RetconRequest(description=r.description, conflicting_entry_ids=r.conflicting_entry_ids,
                                    proposed_resolution=r.proposed_resolution)
                await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)
        active_secret_ids = {s.id for s in ctx.get("secrets", [])}
        await self._commit_knowledge_intents(
            out.knowledge_intents, active_secret_ids, allowed_actions=frozenset({"learn"})
        )
        # Use the in-memory seen_ids (not a fresh re-read): the ReadStore only
        # updates on the Projector's periodic catch_up, so a character minted
        # earlier in this same commit() would be invisible to a re-read here,
        # silently dropping an arc declare for a character created moments ago.
        character_ids = seen_ids
        # Known limitation: this active_arc_ids re-read has the same theoretical
        # staleness for a declare-then-advance-in-one-pass (an arc declared above
        # in this same commit() won't appear here either) -- left as-is since
        # arc actions are rarer than character mentions; the next tick picks it up.
        active_arc_ids = {a.id for a in await self._read.list_arcs(active_only=True)}
        active_beat_ids = {b.id for b in ctx.get("beats", [])}
        await self._commit_arc_intents(
            out.arc_intents, active_arc_ids, character_ids, active_beat_ids, chapter_id=""
        )
        await self._remark(out.feed_note)

    async def _run(self) -> None:
        fp_seen = await self._fingerprint()
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)
        fp_now = await self._fingerprint()
        # The chapter components (count, latest id) are purely external — the
        # Keeper never writes chapters. If they moved mid-run, this run's
        # analysis did not cover the new prose: leave the watermark clear so
        # the next tick re-dispatches. Own retcon filings land in fp_now and
        # are absorbed.
        if fp_now[:2] == fp_seen[:2]:
            self._last_fingerprint = fp_now
        else:
            self._clear_watermark()


def build_character_keeper_runner(settings, callbacks=None, backend=None, tools=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=KeeperOutput,
            backend=backend, tools=tools,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=KeeperOutput)
