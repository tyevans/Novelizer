from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE, DEFAULT_PASS_REMARK, PASS_PROMPT_INSTRUCTION
from agent_kit import GRAPH_RECURSION_LIMIT
from novelizer.agents.schemas import WorldEntriesDraft
from novelizer.canon_fs.skills_route import CRAFT_SKILLS
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.brain.context import chapter_map_note
from novelizer.muse.prompts import architect_settings_note
from novelizer.store.models import WorldEntry, Flag

# Generative lane: the traced failure was an Architect that named three entries
# it meant to write, then spent the remaining fifty-odd turns re-asking whether
# to write them and canonized nothing. The note names the bar that ENDS the
# survey rather than urging speed.
DECISIVENESS_NOTE = """

## When the survey is over
Your survey is finished the moment you can name each entry you mean to write and point at the
chapter or entry that makes it needed. That is the whole bar — a shortlist you can justify is a
decision, not a proposal to yourself. Write those entries. Re-opening the question of WHICH
entries to write, once you have already answered it with evidence, is not diligence: it spends
the pass you were given to canonize lore and ends it with no lore. If something you read genuinely
changes the shortlist, revise it once, say so in your feed note, and write the revised list — do
not start the survey again. A file you have already read this pass has not changed since you read
it; re-reading it in place of emitting is how a pass produces nothing."""

SYSTEM_PROMPT = """You are the World Architect for a living, ever-expanding fictional world. You
grow the world's lore — geography, factions, history, systems, cosmology — so the story always has
grounded material to draw on. You are additive: you expand the world, you never contradict what is
already canon and never overwrite it.

## Your lane
You create WORLD ENTRIES: places, factions, institutions, historical events, physical or
metaphysical systems, cultures, and the rules that govern them.
Good lore is STORY-SERVING. Prioritize what recent chapters have touched but canon does not yet
cover — a place a scene visited, a faction a character named, a system the plot leaned on — over
inventing disconnected regions no chapter needs. Grounded generativity beats encyclopedia-padding.

## Not your lane
- You do NOT write plot or narrate events. An entry describes what the world IS, not what happens
  in the story. Chapters and plot threads are the Author's.
- You do NOT create or name characters — named people are the Character Keeper's canon. You may
  reference a faction or a role, never mint a person.
- You do NOT retcon or amend existing entries. If you find a contradiction, do not fix it and do
  not set supersedes_id: repairing canon is the Retconner's job. Mention it in your feed note.

## How to work — survey first, then emit
Do not write entries from the pushed summary alone; it is an index, not the source.
1. SURVEY. Read the most recent chapters (grep/glob to locate, then read_file) to see which places,
   factions and systems the story is actually leaning on. Use search_canon for thematic gaps ("what
   governs X?") and grep for exact names. Before canonizing anything, list the existing entries in
   that domain and confirm your entry neither duplicates nor contradicts one. If it would, drop it
   or narrow it to genuinely new, consistent material.
2. EMIT. Only after reading, return 1-3 entries. An entry you cannot ground in something you read
   is padding — cut it. Once you can say why each entry is needed, stop searching and emit.

## Each entry
- title: concrete and evocative (not "The Northern Region").
- body: 2-4 paragraphs of specific lore a chapter could be written against. Prose only — no headers
  or bullet lists inside the body.
- domain: one of physical, social, metaphysical, historical, other.
- tags: a few lowercase topic tags.

## Curation flags — flag, don't fix
You never edit or delete existing entries yourself. But when your survey of canon reveals a
world entry that should be curated, file a flag for the Curator to resolve:
- Two or more entries that clearly overlap or duplicate each other → category "world_redundancy",
  related_entry_ids naming them, proposed_resolution "merge".
- An entry filed under the wrong domain or carrying stale/wrong tags → category "world_relevance",
  naming the entry, proposed_resolution describing the correct classification.
File these in your `flags` output; do not act on them.""" + DECISIVENESS_NOTE + PASS_PROMPT_INSTRUCTION + """
Never set no_action when director seeds are present — a seed is always your work."""


class WorldArchitect(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
        pull_mode: bool = False,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="world_architect", personality=personality)
        self.pull_mode = pull_mode

    async def readiness(self) -> float:
        # A pending director seed always wakes the Architect, watermark or
        # not -- the seed is external input the fingerprint doesn't track,
        # so gating on it here would risk starving it indefinitely.
        if await self._read.list_unconsumed_signals(target_agent=self.name):
            return 1.0
        count = len(await self._read.list_world_entries())
        score = max(0.2, 1.0 - count / 50)
        return await self._gate_on_watermark(score)

    async def _fingerprint(self) -> tuple:
        """Gate on the story moving, not the clock. Without this the Architect
        re-fires on an unchanged novel and pads the world with lore no chapter
        asked for. Chapters only, deliberately: entries are this agent's own
        writes, and the read-store projection they land in lags a commit by
        design (see the background projector loop), so comparing a
        just-committed entry count against a pre-commit snapshot would be
        comparing stale data against itself. Chapters are purely external --
        the Architect never writes them -- so they're immune to that lag."""
        chapters = await self._read.list_chapters()
        return (len(chapters), chapters[-1].id if chapters else "")

    async def poll(self) -> dict:
        return {
            "entries": await self._read.list_world_entries(),
            "chapters": await self._read.list_chapters(),
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
            "hand": await self._read.get_active_hand(),
            "summaries": await self._read.list_chapter_summaries(),
        }

    async def work(self, ctx: dict) -> WorldEntriesDraft | None:
        if self.pull_mode:
            # Domains and titles only: the prompt orders a tool-based survey
            # before canonizing anything, and pushed body slices are the
            # summary that survey replaces.
            existing = "\n".join(f"- [{e.domain}] {e.title}" for e in ctx["entries"][:20]) or "The world is empty."
        else:
            existing = "\n".join(f"- [{e.domain}] {e.title}: {e.body[:100]}" for e in ctx["entries"][:20]) or "The world is empty."
        seeds = "\n".join(f"Director seed: {s.body}" for s in ctx["signals"]) or "None."
        cast = self._guarded_line("In character", self.personality)
        sparks = architect_settings_note(ctx.get("hand"))
        # The story the world is for. Lore that serves the chapters beats lore
        # invented in a vacuum, and the Architect could not previously see a
        # single chapter.
        chapters = ctx.get("chapters") or []
        gists = {s.chapter_id: s.gist for s in ctx.get("summaries", []) if s.gist}
        story = (
            f"\n\nChapter index (read these to see what the story needs):\n{chapter_map_note(chapters, gists=gists)}"
            if chapters else ""
        )
        msg = (
            f"Existing world entries:\n{existing}{story}\n\nDirector seeds:\n{seeds}"
            f"{sparks}{cast}\n\nGenerate new world entries."
        )
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, draft: WorldEntriesDraft | None, ctx: dict) -> None:
        if draft is not None and draft.no_action and not ctx["signals"]:
            # Honored only with no pending seeds: a pass must never silently
            # consume (or strand) director input.
            await self._remark(draft.feed_note or DEFAULT_PASS_REMARK)
            self.note_pass()
            return
        if draft is not None:
            for e in draft.entries:
                entry = WorldEntry(title=e.title, body=e.body, domain=e.domain, tags=e.tags)
                await self._committer.commit(self.name, EventType.WORLD_ENTRY_CREATED, entry.id, entry)
            if draft.flags:
                open_flags = await self._read.list_flags(status="open")
                seen_descriptions = {f.description for f in open_flags}
                for r in draft.flags:
                    if r.description in seen_descriptions:
                        continue
                    seen_descriptions.add(r.description)
                    flag = Flag(category=r.category, filed_by=self.name, description=r.description,
                                related_entry_ids=r.related_entry_ids, proposed_resolution=r.proposed_resolution)
                    await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
            await self._remark(draft.feed_note)
        await self._consume_signals(ctx["signals"])

    async def _run(self) -> None:
        fp_seen = await self._fingerprint()
        ctx = await self.poll()
        draft = await self.work(ctx)
        await self.commit(draft, ctx)
        fp_now = await self._fingerprint()
        # If chapters moved mid-run, this run's worldbuilding did not
        # account for the newest prose: leave the watermark clear so the
        # next tick re-dispatches instead of wrongly absorbing it.
        if fp_now == fp_seen:
            self._last_fingerprint = fp_now
        else:
            self._clear_watermark()


def build_world_architect_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
    from deepagents import create_deep_agent
    from agent_kit import build_chat_model
    from agent_kit import ExcludeToolsMiddleware
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + OUTPUT_CONVENTIONS_NOTE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=WorldEntriesDraft,
            backend=backend, tools=tools, skills=CRAFT_SKILLS, subagents=subagents,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=WorldEntriesDraft)


from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant, SubagentGrant


def _construct(ctx: AgentContext) -> WorldArchitect:
    enabled = ctx.settings.world_architect_tools_enabled
    subagent_enabled = ctx.settings.world_architect_subagent_enabled
    builder = ctx.tooled(build_world_architect_runner, enabled, subagent_enabled, "world_architect")
    runner = ctx.runner_for("world_architect", builder)
    return WorldArchitect(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.default_agent_interval,
        personality=ctx.personalities.get("world_architect", ""),
        pull_mode=enabled,
    )


SPEC = AgentSpec(
    name="world_architect",
    tool_grant=ToolGrant(enabled_setting="world_architect_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="world_architect_subagent_enabled"),
    construct=_construct,
    rebuild_on=("agent_temperature",),
)
