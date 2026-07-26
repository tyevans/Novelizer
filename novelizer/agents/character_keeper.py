from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.prompts import (
    OUTPUT_CONVENTIONS_NOTE, DEFAULT_PASS_REMARK, PASS_PROMPT_INSTRUCTION, SPEECH_MARKER_NOTE,
)
from agent_kit import GRAPH_RECURSION_LIMIT
from novelizer.agents.schemas import KeeperOutput
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.brain.context import arc_note, chapter_map_note, open_retcons_note
from novelizer.brain.context_assembly import assemble_verbatim, CharHeuristicEstimator
from novelizer.brain.watermarks import current_done_ids
from novelizer.canon_fs.skills_route import CRAFT_SKILLS
from novelizer.canon.characters import slugify_character_name
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, InspirationUptakeRecorded, ChapterProcessed
from novelizer.store.models import Character, Flag, FlagStatus
from novelizer.muse.prompts import NAME_UPTAKE_HAND_WINDOW, name_uptake_matches

logger = logging.getLogger(__name__)

# Guided, but framed as boundedness rather than speed: this agent's central
# instruction ("de-duplication is the job") is the kind of open-ended-sounding
# duty that invites unlimited checking, when the check itself is finite -- the
# cast list and the aliases either hold that person or they do not.
DECISIVENESS_NOTE = """

## The de-duplication check has an end
Proving a character is new is a bounded check, not an open question: the cast list and every
character's aliases either contain that person under some label or they do not, and once you have
looked at both you have your answer — emit it. A sheet update settles the same way; the prose you
read either shows the change or it does not. A file you have already read this pass has not changed
since you read it, so re-reading it in place of emitting is how a pass ends with a cast nobody
updated. Rigour here means checking the aliases once, properly, not checking them repeatedly."""

SYSTEM_PROMPT = """You are the Character Keeper for a living fictional world. You maintain the
canonical cast: you discover the characters the prose introduces, keep each sheet true to what
recent chapters show, tend their arcs, and record when a character learns a secret. You work from
what the prose actually says, never from what you expect it to say.

## Your lane
- Discover new_characters: named people who appear in the prose but are missing from the cast.
  Spell each name exactly as the prose spells it, and give the traits, motivations, backstory,
  arc_status and voice the prose itself shows — leave a field blank rather than invent it.
- Update existing characters: revise arc_status, and correct traits/motivations/backstory/voice,
  to match recent chapters. Record voice as concrete dialogue patterns, vocabulary and verbal tics
  you could quote, and refine it as a voice evolves.
- Tend arcs: declare or advance each significant character's planned arc — the lie they believe,
  want vs need, arc type — pivot on blueprint beats, and resolve the arc when the story settles it.
  Cite arc and beat ids exactly.
- Record knowledge: when a chapter shows a character learning a secret ON THE PAGE, emit a
  secret_citation (action="learn", the secret's id, the character's id). A character merely acting
  on a secret is not a learning moment and is not yours to record. When your context lists no
  active secrets there is no id to cite, so emit no secret_citations at all — never invent one.
- Flag character contradictions: when a canonical trait and a prose action genuinely conflict, file
  a retcon_request (what conflicts with what, the conflicting ids, a proposed resolution).

## Not your lane
- You do not write or rewrite prose, and you do not invent characters, arcs or events the prose
  does not show. That is the Author's work.
- You do not chase timeline, factual or world-logic contradictions — dates, locations, quantities,
  anachronisms. That is the Continuity Checker. Your retcons are strictly about a named character
  behaving against their established sheet.
- You do not resolve retcons or amend canon entries: you file, the Retconner repairs.
- You do not plant, reveal or invent secrets — only record a character learning an existing one.

## De-duplication is the job
Before reporting a new character, prove they are new. Check the cast list AND each character's
aliases for the same person under a nickname, a title, a first-name-only reference, or a variant
spelling ("Doc" for "Dr. Reyes", "the sergeant" for a named soldier). If the prose reveals a new
name for an existing character, do not create a duplicate — record it as an update. Re-reporting an
existing person under a new label is the failure mode to avoid.

## Output
Return new_characters, updated_characters (id + revised arc_status, plus any corrected
traits/motivations/backstory/voice), retcon_requests, arc intents, and secret_citations (learn
only -- you have no field for planting a secret, by design). You may be shown retcon requests already filed and still open: do not re-report those, even
reworded.""" + DECISIVENESS_NOTE + PASS_PROMPT_INSTRUCTION

# Appended only in the tooled build: the base retrieval note assumes a pushed
# summary is enough, which is exactly what starved discovery here.
KEEPER_PULL_NOTE = (
    "\n\n## Canon access\n"
    "You have file tools over the story canon (ls, read_file, grep, glob) and semantic search "
    "(search_canon). The cast and chapter index below are a MAP, not the source — the chapter "
    "lines are titles and ids only. Work in two phases.\n"
    "1. RESEARCH: read every chapter new since your last pass IN FULL with "
    "read_file(path, limit=2000) — read_file returns only the first 100 lines without that, "
    "and most chapters are longer. A character "
    "can be introduced in a chapter's last line, so never judge a chapter from its title, an "
    "excerpt, or a window that ended in a TRUNCATED notice. "
    "Use grep to check whether a name you are about to report already exists as a "
    "character's name or alias. Read a character's file when you need their current sheet.\n"
    "2. EMIT: only once you have read the prose behind a finding, produce the structured output. "
    "Ground each new character and each contradiction in the chapter you read it in. When your "
    "findings are grounded, stop searching and emit — do not keep browsing. Cite ids exactly as "
    "shown in frontmatter or search results."
)

UNREAD_CHAPTERS_HEADING = "Unread chapters (full prose — mine these now):"


def _merge_outputs(outs: list[KeeperOutput]) -> KeeperOutput:
    """Merge the per-window outputs of an oversize chapter's sweep into one.
    Cross-window duplicates are safe: slugs mint exactly once via the
    commit-time seen_ids re-read; flags dedup by description."""
    merged = KeeperOutput()
    for o in outs:
        merged.new_characters.extend(o.new_characters)
        merged.updated_characters.extend(o.updated_characters)
        merged.flags.extend(o.flags)
        merged.secret_citations.extend(o.secret_citations)
        merged.arc_intents.extend(o.arc_intents)
        merged.feed_note = o.feed_note or merged.feed_note
    merged.no_action = all(o.no_action for o in outs) if outs else False
    return merged


class CharacterKeeper(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        event_store: EventStore,
        interval: int = 120,
        personality: str = "",
        pull_mode: bool = False,
        extractor_token_budget: int = 24000,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="character_keeper", personality=personality)
        # Discovery needs the whole chapter -- a character introduced in the
        # final scene is as canonical as one in the opening line -- so the
        # Keeper watermark-sweeps unmined chapters in full, in both modes,
        # instead of trusting any fixed prose cap (character-discovery-fix).
        self._events = event_store
        self.pull_mode = pull_mode
        self._budget = extractor_token_budget

    async def readiness(self) -> float:
        chars = await self._read.list_characters()
        chapters = await self._read.list_chapters()
        if chapters and not chars:
            # Prose exists but the cast is empty: bootstrapping the cast is
            # the Keeper's most urgent work — nothing else mints characters.
            return 0.8
        score = 0.5 if (chars and chapters) else 0.2
        return await self._gate_on_watermark(score)

    async def _unmined(self, chapters: list) -> list:
        processed = await self._events.events_since(0, event_types=[EventType.CHAPTER_PROCESSED])
        own = [e for e in processed if e.payload.get("agent") == self.name]
        revised = await self._events.events_since(0, event_types=[EventType.CHAPTER_REVISED])
        done = current_done_ids(own, revised)
        return [c for c in chapters if c.id not in done]

    async def _fingerprint(self) -> tuple:
        chapters = await self._read.list_chapters()
        open_retcons = await self._read.list_flags(category="contradiction", status=FlagStatus.open)
        unmined = await self._unmined(chapters)
        return (len(chapters), chapters[-1].id if chapters else "", len(open_retcons), len(unmined))

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "characters": await self._read.list_characters(),
            "recent": chapters[-5:],
            "chapters": chapters,
            "unmined": await self._unmined(chapters),
            "secrets": await self._read.list_secrets(),
            "open_retcons": await self._read.list_flags(category="contradiction", status=FlagStatus.open),
            "hands": (await self._read.list_hands(status="consumed"))[-NAME_UPTAKE_HAND_WINDOW:],
            "arcs": await self._read.list_arcs(active_only=True),
            "all_arcs": await self._read.list_arcs(active_only=False),
            "beats": await self._read.list_beats(),
            "blueprint": await self._read.get_active_blueprint(),
            "summaries": await self._read.list_chapter_summaries(),
        }

    def _select_unmined(self, unmined: list) -> list:
        """Greedy oldest-first budget selection: the first chapter is always
        selected even if it alone exceeds the budget (an oversize chapter
        still gets mined, just windowed); later chapters are only added
        while they still fit."""
        est = CharHeuristicEstimator()
        selected: list = []
        spent = 0
        for c in unmined:
            cost = est.estimate(c.prose)
            if selected and spent + cost > self._budget:
                break
            selected.append(c)
            spent += cost
        return selected

    async def work(self, ctx: dict) -> KeeperOutput | None:
        if not ctx["characters"] and not ctx["unmined"]:
            return None
        # Aliases inline: the dedup rule ("never re-report someone under a
        # nickname") is unrunnable if the nicknames aren't in front of it.
        chars = "\n".join(
            f"- {c.name} (id:{c.id}): traits={c.traits}, arc={c.arc_status}"
            + (f", also known as {', '.join(c.aliases)}" if c.aliases else "")
            for c in ctx["characters"]
        ) or "None yet."
        selected = self._select_unmined(ctx["unmined"])
        ctx["processed_now"] = [c.id for c in selected]
        secrets_block = ""
        if ctx.get("secrets"):
            listing = "\n".join(f"- {s.id} ('{s.title}')" for s in ctx["secrets"])
            secrets_block = (
                "\n\nActive secrets — when a chapter shows a character learning a secret "
                "on the page, cite its id in a secret_citation:\n" + listing
            )
        cast = self._guarded_line("In character", self.personality)
        retcons = open_retcons_note(ctx.get("open_retcons", []))
        names_by_id = {c.id: c.name for c in ctx["characters"]}
        arcs_lines = "\n".join(
            f"- {names_by_id.get(arc.character_id, arc.character_id)}: {arc.arc_type} arc "
            f"(id:{arc.id}) lie='{arc.lie}' advances={arc.advance_count}"
            + (f" [resolved:{arc.outcome}]" if arc.resolved else "")
            for arc in ctx.get("arcs", [])
        )
        beats = ctx.get("beats", [])
        if beats:
            arcs_lines += f"\nAvailable beat ids for pivots: {', '.join(b.id for b in beats)}"
        arcs_block = f"\n\nActive arcs:\n{arcs_lines}" if arcs_lines else ""
        note = arc_note(
            ctx.get("all_arcs", []), ctx["characters"], ctx.get("chapters", []), beats, ctx.get("blueprint"),
        )
        rejections = await self._own_rejections_note()
        tail = f"{secrets_block}{retcons}{rejections}{cast}{arcs_block}{note}"

        def build_msg(prose_block: str) -> str:
            if self.pull_mode:
                chapters_section = (
                    f"Chapter index:\n"
                    f"{chapter_map_note(ctx['chapters'], gists={s.chapter_id: s.gist for s in ctx['summaries'] if s.gist})}"
                    f"\n\n{UNREAD_CHAPTERS_HEADING}\n{prose_block}"
                )
            else:
                chapters_section = f"Recent chapters:\n{prose_block}"
            return f"Characters:\n{chars}\n\n{chapters_section}{tail}"

        # Window per chapter. Selection guarantees at most the first selected
        # chapter can be oversize (a later chapter is only added if it still
        # fits the whole remaining budget), so a >1-window case is always a
        # lone oversize chapter -- mined with one runner call per window,
        # merged. Otherwise every selected chapter contributes exactly one
        # window and they are joined into a single prompt/call.
        windows_by_chapter = [(c, assemble_verbatim(c.prose, self._budget)) for c in selected]
        if len(selected) == 1 and len(windows_by_chapter[0][1]) > 1:
            chapter, windows = windows_by_chapter[0]
            outs = []
            for w in windows:
                part = f" (part {w.index + 1}/{w.total})"
                label = f"Chapter '{chapter.title}'{part}"
                result = await self._runner.ainvoke(
                    {"messages": [{"role": "user", "content": build_msg(f"{label}:\n{w.text}")}]}
                )
                outs.append(result.get("structured_response"))
            if any(o is None for o in outs):
                return None
            return _merge_outputs(outs)
        blocks = []
        for c, windows in windows_by_chapter:
            for w in windows:
                part = f" (part {w.index + 1}/{w.total})" if w.total > 1 else ""
                blocks.append(f"Chapter '{c.title}'{part}:\n{w.text}")
        prose_block = "\n\n".join(blocks) or "None."
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": build_msg(prose_block)}]})
        return result.get("structured_response")

    async def commit(self, out: KeeperOutput | None, ctx: dict) -> None:
        if out is None:
            return
        if out.no_action:
            await self._remark(out.feed_note or DEFAULT_PASS_REMARK)
            self.note_pass()
            await self._stamp_processed(ctx)
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
        if out.flags:
            # Re-read the queue at commit time (not from ctx): the LLM call in
            # work() is slow enough that another agent may have filed meanwhile.
            open_reqs = await self._read.list_flags(category="contradiction", status=FlagStatus.open)
            seen_descriptions = {r.description for r in open_reqs}
            for r in out.flags:
                if r.description in seen_descriptions:
                    continue
                seen_descriptions.add(r.description)
                flag = Flag(category=r.category, filed_by=self.name, description=r.description,
                            related_entry_ids=r.related_entry_ids, proposed_resolution=r.proposed_resolution)
                await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
        # Use the in-memory seen_ids (not a fresh re-read): the ReadStore only
        # updates on the Projector's periodic catch_up, so a character minted
        # earlier in this same commit() would be invisible to a re-read here,
        # silently dropping an arc declare -- or a `learn` intent -- for a
        # character created moments ago. This is the whole reason the roster is
        # computed here rather than inside each commit helper.
        character_ids = seen_ids
        active_secret_ids = {s.id for s in ctx.get("secrets", [])}
        await self._commit_secret_citations(
            out.secret_citations, active_secret_ids, allowed_actions=frozenset({"learn"}),
            character_ids=character_ids,
        )
        # Known limitation: this active_arc_ids re-read has the same theoretical
        # staleness for a declare-then-advance-in-one-pass (an arc declared above
        # in this same commit() won't appear here either) -- left as-is since
        # arc actions are rarer than character mentions; the next tick picks it up.
        active_arc_ids = {a.id for a in await self._read.list_arcs(active_only=True)}
        active_beat_ids = {b.id for b in ctx.get("beats", [])}
        await self._commit_arc_intents(
            out.arc_intents, active_arc_ids, character_ids, active_beat_ids,
            chapter_id=ctx["recent"][-1].id if ctx["recent"] else "",
        )
        await self._remark(out.feed_note)
        await self._stamp_processed(ctx)

    async def _stamp_processed(self, ctx: dict) -> None:
        # Presented-in-full-and-judged still counts as processed, including
        # on a no_action pass: the chapter was shown in full and the Keeper
        # chose not to act on it, so it should not be re-shown every tick.
        for chapter_id in ctx.get("processed_now", []):
            await self._committer.commit(
                self.name, EventType.CHAPTER_PROCESSED, chapter_id,
                ChapterProcessed(agent=self.name, chapter_id=chapter_id),
            )

    async def _run(self) -> None:
        fp_seen = await self._fingerprint()
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)
        fp_now = await self._fingerprint()
        # The chapter components (count, latest id) are purely external — the
        # Keeper never writes chapters. If they moved mid-run, this run's
        # analysis did not cover the new prose: leave the watermark clear so
        # the next tick re-dispatches. unmined > 0 at record time means the
        # budgeted sweep has backlog left (or a failed pass must retry) — the
        # gate must stay open until the drain finishes, same contract as the
        # miner's fp_now[2] check. Own retcon filings land in fp_now and are
        # absorbed.
        if fp_now[3] == 0 and fp_now[:2] == fp_seen[:2]:
            self._last_fingerprint = fp_now
        else:
            self._clear_watermark()


def build_character_keeper_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
    from deepagents import create_deep_agent
    from agent_kit import build_chat_model
    from agent_kit import ExcludeToolsMiddleware
    from novelizer.agents.middleware import tool_call_budget
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        system_prompt = SYSTEM_PROMPT + KEEPER_PULL_NOTE + SPEECH_MARKER_NOTE + OUTPUT_CONVENTIONS_NOTE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=KeeperOutput,
            backend=backend, tools=tools, skills=CRAFT_SKILLS, subagents=subagents,
            middleware=[tool_call_budget(),
                        ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=KeeperOutput)


from novelizer.agents.registry_types import AgentContext, AgentSpec, AgentTier, ToolGrant, SubagentGrant


def _construct(ctx: AgentContext) -> CharacterKeeper:
    enabled = ctx.settings.character_keeper_tools_enabled
    subagent_enabled = ctx.settings.character_keeper_subagent_enabled
    builder = ctx.tooled(build_character_keeper_runner, enabled, subagent_enabled, "character_keeper")
    runner = ctx.runner_for("character_keeper", builder)
    return CharacterKeeper(
        runner, ctx.read, ctx.committer, ctx.events,
        interval=ctx.settings.default_agent_interval,
        personality=ctx.personalities.get("character_keeper", ""),
        pull_mode=enabled,
        extractor_token_budget=ctx.settings.extractor_token_budget,
    )


SPEC = AgentSpec(
    name="character_keeper",
    tool_grant=ToolGrant(enabled_setting="character_keeper_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="character_keeper_subagent_enabled"),
    construct=_construct,
    tier=AgentTier.FULL,
    rebuild_on=("agent_temperature",),
)
