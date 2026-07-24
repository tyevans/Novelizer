from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from agent_kit import GRAPH_RECURSION_LIMIT
from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE
from novelizer.agents.schemas import PlotterOutput
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.brain.beat_drift import beat_drifts
from novelizer.brain.completion import completion_status
from novelizer.brain.context import (
    arc_note, beat_drift_note, chapter_map_note, completion_note, finale_convergence_note,
    ledger_note, resolution_pacing_note, stale_threads_note, tension_target_note,
)
from novelizer.canon.beat_templates import beat_window
from novelizer.canon_fs.skills_route import CRAFT_SKILLS
from novelizer.canon.events import BookCompleted, ChapterBriefSuperseded, EventType
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.muse.prompts import inspiration_note
from novelizer.store.models import Flag, SignalKind

logger = logging.getLogger(__name__)

PLOTTER_SYSTEM_PROMPT = """You are the Plotter — the writers' room's showrunner. You do not write prose.
You keep the story aimed at a shape: propose a blueprint from the premise before any prose or
world exists — you go first; pick a framework and target length that fit the premise, genre and
(if any) the world so far; keep 1-3 chapter briefs drafted ahead of the
Author (each with a goal, threads to touch, beats to hit, a value shift, and a planned outcome
biased toward yes_but/no_and), judge when a drafted chapter fulfilled a beat, plan resolution
windows for threads and secret reveals, and plant or re-window promises. Revise briefs freely;
supersede rather than contradict. Cite every id exactly as shown. Prefer steering the story
toward overdue payoffs and dark threads over introducing new material. If the story clearly
needs more or fewer chapters than the active blueprint assumes, retarget it rather than
forcing the remaining beats into the wrong-sized frame."""

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
    arcs = ctx.get("arcs", [])
    characters = ctx.get("characters", [])

    gists = {s.chapter_id: s.gist for s in ctx.get("summaries", []) if s.gist}
    blocks = [f"Chapter index:\n{chapter_map_note(chapters, gists=gists)}"]

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
        completion_note(blueprint, beats, promises, arcs, chapters, characters),
        finale_convergence_note(blueprint, beats, promises, arcs, chapters, characters),
        stale_threads_note(threads, chapters),
        beat_drift_note(blueprint, beats, chapters),
        tension_target_note(blueprint, beats, ctx.get("scores", []), chapters),
        arc_note(arcs, characters, chapters, beats, blueprint),
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
        blueprint = await self._read.get_active_blueprint()
        if blueprint is None:
            # Outline-first: the Plotter goes before anyone, minting the first
            # blueprint from the premise alone. Stand down once we've proposed
            # and are only waiting on Director approval.
            proposals = await self._read.list_proposals(status="open")
            if any(p.target_event_type == EventType.BLUEPRINT_ADOPTED for p in proposals):
                return 0.0
            seeds = await self._read.list_unconsumed_signals(target_agent=self.name)
            if seeds or chapters or world:
                return 1.0
            return 0.0
        # steady state below (unchanged)
        open_briefs = await self._read.list_briefs("open")
        chapter_count = len(chapters)
        open_briefs_ahead = sum(
            1 for b in open_briefs
            if chapter_count < b.target_ordinal <= chapter_count + _READINESS_BRIEF_LOOKAHEAD
        )
        needed = max(0, _READINESS_BRIEF_RUNWAY - open_briefs_ahead)
        runway = min(1.0, needed / _READINESS_BRIEF_RUNWAY)
        if runway < 1.0 and blueprint is not None:
            beats = await self._read.list_beats()
            drifts = beat_drifts(blueprint, beats, chapters)
            if any(d.kind == "late" for d in drifts):
                return max(runway, 0.9)
        return runway

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
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
        arcs = await self._read.list_arcs(active_only=False)
        return {
            "chapters": chapters,
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
            "arcs": arcs,
            "summaries": await self._read.list_chapter_summaries(),
        }

    async def work(self, ctx: dict) -> PlotterOutput | None:
        summary = _summarize(ctx, self.personality)
        msg = f"Plan the story's shape:\n{summary}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: PlotterOutput | None, ctx: dict) -> None:
        await self._reap_stale_open_briefs(ctx["open_briefs"], len(ctx["chapters"]))
        await self._declare_completion_if_satisfied(out, ctx)

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

        if out.retarget_intent is not None:
            await self._commit_retarget_intent(out.retarget_intent, ctx["blueprint"])

        active_thread_ids = {t.id for t in ctx["threads"]}
        active_beat_ids = {b.id for b in ctx["beats"]}
        active_promise_ids = {p.id for p in ctx["promises"]}
        valid_chapter_ids = {c.id for c in ctx["chapters"]}
        unrevealed_secret_ids = {s.id for s in ctx["secrets"] if not s.revealed}

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
        if out.flags:
            open_flags = await self._read.list_flags(status="open")
            seen_descriptions = {f.description for f in open_flags}
            for r in out.flags:
                if r.description in seen_descriptions:
                    continue
                seen_descriptions.add(r.description)
                flag = Flag(category=r.category, filed_by=self.name, description=r.description,
                            related_entry_ids=r.related_entry_ids, proposed_resolution=r.proposed_resolution)
                await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
        await self._remark(out.feed_note)
        # Leave premise seeds for the World Architect (seed -> world is its job);
        # the Plotter only reads them to shape the blueprint. Consume everything
        # else targeted at the Plotter as before.
        await self._consume_signals([s for s in ctx["signals"] if s.kind != SignalKind.seed])

    async def _reap_stale_open_briefs(self, open_briefs: list, drafted_chapter_count: int) -> None:
        """Mechanically supersede open briefs whose target_ordinal has already
        been drafted past -- deterministic housekeeping, not dependent on the
        LLM emitting a supersede intent. Runs before any LLM-driven brief
        commits so a stale brief never lingers just because the model had
        nothing else to say this pass.

        Idempotency note: if an LLM brief intent this same pass (or a prior
        cached one) cites a brief_id just reaped here, re-committing it to
        CHAPTER_BRIEF_SUPERSEDED is a projection no-op -- not because this
        helper filters already-superseded ids, but because the projector's
        open-status guard makes SUPERSEDED an absorbing state."""
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

    async def _declare_completion_if_satisfied(self, out: PlotterOutput | None, ctx: dict) -> None:
        """Mechanical, deterministic declaration -- no LLM involvement, like
        _reap_stale_open_briefs. Must run even when the LLM returned nothing
        useful (out is None), so it lives beside the reap, above the
        `out is None` guard in commit()."""
        blueprint = ctx["blueprint"]
        if blueprint is None or blueprint.completed:
            return
        status = completion_status(blueprint, ctx["beats"], ctx["promises"], ctx["arcs"], ctx["chapters"])
        if status is None or not status.complete:
            return
        chapters = ctx["chapters"]
        chapter_id = chapters[-1].id if chapters else ""
        note = out.feed_note[:200] if out is not None else ""
        await self._committer.commit(
            self.name, EventType.BOOK_COMPLETED, blueprint.id,
            BookCompleted(blueprint_id=blueprint.id, chapter_id=chapter_id, note=note),
        )

    async def _run(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_plotter_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
    """Mirror build_structure_analyst_runner, but the Plotter keeps
    write_todos: no ExcludeToolsMiddleware here, since the planner benefits
    from todo tracking per the pull-tools spec's "where it plausibly helps"
    clause."""
    from deepagents import create_deep_agent
    from agent_kit import build_chat_model
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        from novelizer.agents.middleware import TodoContextMiddleware
        system_prompt = PLOTTER_SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + OUTPUT_CONVENTIONS_NOTE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=PlotterOutput,
            backend=backend, tools=tools, skills=CRAFT_SKILLS, subagents=subagents,
            middleware=[TodoContextMiddleware()],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=PLOTTER_SYSTEM_PROMPT, response_format=PlotterOutput)


from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant, SubagentGrant


def _construct(ctx: AgentContext) -> Plotter:
    enabled = ctx.settings.plotter_tools_enabled
    subagent_enabled = ctx.settings.plotter_subagent_enabled
    builder = ctx.tooled(build_plotter_runner, enabled, subagent_enabled, "plotter")
    runner = ctx.runner_for("plotter", builder)
    return Plotter(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.plotter_interval,
        personality=ctx.personalities.get("plotter", ""),
    )


SPEC = AgentSpec(
    name="plotter",
    tool_grant=ToolGrant(enabled_setting="plotter_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="plotter_subagent_enabled"),
    construct=_construct,
)
