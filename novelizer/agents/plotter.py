from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner, GRAPH_RECURSION_LIMIT
from novelizer.agents.schemas import PlotterOutput
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.brain.context import (
    beat_drift_note, chapter_map_note, ledger_note, resolution_pacing_note, stale_threads_note,
    tension_target_note,
)
from novelizer.canon.beat_templates import beat_window
from novelizer.canon.events import ChapterBriefSuperseded, EventType
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.muse.prompts import inspiration_note

logger = logging.getLogger(__name__)

PLOTTER_SYSTEM_PROMPT = """You are the Plotter — the writers' room's showrunner. You do not write prose.
You keep the story aimed at a shape: propose a blueprint when none exists (pick a framework
and target length that fit the world and genre), keep 1-3 chapter briefs drafted ahead of the
Author (each with a goal, threads to touch, beats to hit, a value shift, and a planned outcome
biased toward yes_but/no_and), judge when a drafted chapter fulfilled a beat, plan resolution
windows for threads and secret reveals, and plant or re-window promises. Revise briefs freely;
supersede rather than contradict. Cite every id exactly as shown. Prefer steering the story
toward overdue payoffs and dark threads over introducing new material."""

_READINESS_BRIEF_RUNWAY = 2
_READINESS_BRIEF_LOOKAHEAD = 3


def _summarize(ctx: dict, personality: str = "") -> str:
    chapters = ctx["chapters"]
    blueprint = ctx["blueprint"]
    beats = ctx["beats"]
    open_briefs = ctx["open_briefs"]
    threads = ctx["threads"]
    promises = ctx["promises"]
    secrets = ctx["secrets"]
    signals = ctx["signals"]

    blocks = [f"Chapter index:\n{chapter_map_note(chapters)}"]

    if blueprint is None:
        blocks.append("No blueprint adopted — propose one (pick a framework and target length).")
    else:
        beat_lines = []
        for b in beats:
            lo, hi = beat_window(b.ideal_pct, b.tolerance_pct, blueprint.target_chapter_count)
            fulfilled = b.fulfilled_by_chapter_id if b.fulfilled_by_chapter_id else "—"
            beat_lines.append(
                f"- [{b.id}] {b.name} @ch {lo}-{hi} ({b.expected_polarity}) fulfilled: {fulfilled}"
            )
        beats_block = "\n".join(beat_lines) if beat_lines else "(no beats)"
        blocks.append(
            "Active blueprint: framework={framework} target={target} genre={genre}\n{beats}".format(
                framework=blueprint.framework,
                target=blueprint.target_chapter_count,
                genre=blueprint.genre,
                beats=beats_block,
            )
        )

    if open_briefs:
        brief_lines = "\n".join(f"- [{b.id}] ordinal {b.target_ordinal}: {b.goal}" for b in open_briefs)
        blocks.append(f"Open briefs:\n{brief_lines}")
    else:
        blocks.append("Open briefs: none.")

    if threads:
        thread_lines = "\n".join(
            f"- [{t.id}] {t.name} window {t.window_lo}-{t.window_hi}" for t in threads
        )
        blocks.append(f"Threads:\n{thread_lines}")

    for note in (
        ledger_note(promises, chapters),
        resolution_pacing_note(threads, secrets, chapters),
        stale_threads_note(threads, chapters),
        beat_drift_note(blueprint, beats, chapters),
        tension_target_note(blueprint, beats, ctx.get("scores", []), chapters),
    ):
        if note:
            blocks.append(note.strip())

    if signals:
        signal_lines = "\n".join(f"- {s.id} ({s.kind}): {s.body}" for s in signals)
        blocks.append(f"Director signals:\n{signal_lines}")

    hand_note = inspiration_note(ctx["hand"])
    if hand_note:
        blocks.append(hand_note.strip())

    if personality:
        blocks.append(f"In character: {personality}")

    return "\n\n".join(blocks)


class Plotter(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 300,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="plotter", personality=personality)

    async def readiness(self) -> float:
        chapters = await self._read.list_chapters()
        world = await self._read.list_world_entries()
        if not chapters and not world:
            return 0.0
        blueprint = await self._read.get_active_blueprint()
        if chapters and blueprint is None:
            return 1.0
        open_briefs = await self._read.list_briefs("open")
        chapter_count = len(chapters)
        open_briefs_ahead = sum(
            1 for b in open_briefs
            if chapter_count < b.target_ordinal <= chapter_count + _READINESS_BRIEF_LOOKAHEAD
        )
        needed = max(0, _READINESS_BRIEF_RUNWAY - open_briefs_ahead)
        return min(1.0, needed / _READINESS_BRIEF_RUNWAY)

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        world = await self._read.list_world_entries()
        characters = await self._read.list_characters()
        blueprint = await self._read.get_active_blueprint()
        beats = await self._read.list_beats()
        open_briefs = await self._read.list_briefs("open")
        threads = await self._read.list_threads()
        secrets = await self._read.list_secrets()
        promises = await self._read.list_promises()
        signals = await self._read.list_unconsumed_signals(target_agent="plotter")
        hand = await self._read.get_active_hand()
        open_proposals = await self._read.list_proposals(status="open")
        scores = await self._read.list_structure_scores()
        return {
            "chapters": chapters,
            "world": world[:10],
            "characters": characters,
            "blueprint": blueprint,
            "beats": beats,
            "open_briefs": open_briefs,
            "threads": threads,
            "secrets": secrets,
            "promises": promises,
            "signals": signals,
            "hand": hand,
            "open_proposals": open_proposals,
            "scores": scores,
        }

    async def work(self, ctx: dict) -> PlotterOutput | None:
        summary = _summarize(ctx, self.personality)
        msg = f"Plan the story's shape:\n{summary}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: PlotterOutput | None, ctx: dict) -> None:
        if out is None:
            return

        if out.blueprint_plan is not None:
            pending_blueprint_proposal = any(
                p.target_event_type == EventType.BLUEPRINT_ADOPTED for p in ctx["open_proposals"]
            )
            if ctx["blueprint"] is not None:
                logger.warning(
                    "plotter: dropped blueprint plan citing framework %r -- a blueprint is already active",
                    out.blueprint_plan.framework,
                )
            elif pending_blueprint_proposal:
                logger.info(
                    "plotter: dropped blueprint plan citing framework %r -- a blueprint proposal is "
                    "already pending Director approval",
                    out.blueprint_plan.framework,
                )
            else:
                await self._commit_blueprint_plan(out.blueprint_plan)

        active_thread_ids = {t.id for t in ctx["threads"]}
        active_beat_ids = {b.id for b in ctx["beats"]}
        active_promise_ids = {p.id for p in ctx["promises"]}
        valid_chapter_ids = {c.id for c in ctx["chapters"]}
        unrevealed_secret_ids = {s.id for s in ctx["secrets"] if not s.revealed}

        await self._reap_stale_open_briefs(ctx["open_briefs"], len(ctx["chapters"]))

        await self._commit_brief_intents(
            out.brief_intents, ctx["open_briefs"], len(ctx["chapters"]),
            active_thread_ids, active_beat_ids, active_promise_ids,
        )
        await self._commit_beat_intents(out.beat_intents, active_beat_ids, valid_chapter_ids)
        await self._commit_resolution_plan_intents(
            out.resolution_plan_intents, active_thread_ids, unrevealed_secret_ids
        )
        await self._commit_promise_intents(
            out.promise_intents, active_promise_ids, active_thread_ids, chapter_id=""
        )
        await self._remark(out.feed_note)
        await self._consume_signals(ctx["signals"])

    async def _reap_stale_open_briefs(self, open_briefs: list, drafted_chapter_count: int) -> None:
        """Mechanically supersede open briefs whose target_ordinal has already
        been drafted past -- deterministic housekeeping, not dependent on the
        LLM emitting a supersede intent. Runs before any LLM-driven brief
        commits so a stale brief never lingers just because the model had
        nothing else to say this pass."""
        for brief in open_briefs:
            if brief.target_ordinal <= drafted_chapter_count:
                logger.info(
                    "plotter: reaping stale open brief %r (target_ordinal=%r, drafted_chapter_count=%r)",
                    brief.id, brief.target_ordinal, drafted_chapter_count,
                )
                await self._committer.commit(
                    self.name, EventType.CHAPTER_BRIEF_SUPERSEDED, brief.id,
                    ChapterBriefSuperseded(brief_id=brief.id, superseded_by_brief_id=""),
                )

    async def _run(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_plotter_runner(settings, callbacks=None, backend=None, tools=None):
    """Mirror build_structure_analyst_runner, but the Plotter keeps
    write_todos: no ExcludeToolsMiddleware here, since the planner benefits
    from todo tracking per the pull-tools spec's "where it plausibly helps"
    clause."""
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        system_prompt = PLOTTER_SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=PlotterOutput,
            backend=backend, tools=tools,
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=PLOTTER_SYSTEM_PROMPT, response_format=PlotterOutput)
