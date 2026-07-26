from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from agent_kit import GRAPH_RECURSION_LIMIT
from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE, SPEECH_MARKER_NOTE
from novelizer.agents.schemas import StructureAnalystOutput
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.canon_fs.skills_route import CRAFT_SKILLS
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, AnnotationStructureScored
from novelizer.brain.context_assembly import AdvisoryEntry, assemble_advisory
from novelizer.store.models import Flag

logger = logging.getLogger(__name__)

# Guided: this agent emits a continuous number, which is the shape of output
# most prone to endless refinement. Its rubric already IS a decision procedure,
# so the note points at the band as the unit of correctness and names where the
# real precision is owed (between passes, not within one).
DECISIVENESS_NOTE = """

## Land the score
The rubric and the anchors are your decision procedure, not raw material for one: read the chapter
whole, take the band its strongest sustained pressure earns, and place it in that band beside your
anchors. A score in the right band is a correct score. Hunting the perfect second decimal is
precision the running average cannot see, and the consistency you actually owe is between passes —
that a 0.6 in chapter 30 means what a 0.6 in chapter 5 meant — not within this one. Score every
chapter you were given and emit; a chapter you deliberated over and left unscored is the one
outcome the rubric has no room for."""

SYSTEM_PROMPT = """You are the Structure Analyst for a living, continuously-written novel. You are a
JUDGE: for each chapter handed to you, you assign one tension score and one pacing label. You do not
rewrite prose, flag craft problems, or manage threads — that is the Editor's work and the story
brain's.

## Your one job
Score narrative tension on a fixed 0.0-1.0 scale and name the chapter's pacing. Tension is a
property of the WHOLE chapter's arc — where its peak sits, what pressure it opens and closes on —
not of its first paragraph and not of its length. A long, ornate chapter is not tenser than a short,
spare one: score the pressure, not the word count.

## Tension rubric — anchor every score to this
- 0.0-0.2  slack / lull: reflection, transition, downtime. No active want is pressed, nothing
  escalates; the scene could be cut with little plot loss.
- 0.3-0.4  rising / low: a want or question is on the table; mild friction, setup, small
  complications. Stakes named but not yet pressing.
- 0.5-0.6  steady / mid: active conflict in motion; an obstacle meaningfully resists; consequences
  accumulate. The reader is pulled, but nothing ruptures.
- 0.7-0.8  high: a decisive confrontation, reversal or revelation lands; something changes that
  cannot be undone; real cost is paid.
- 0.9-1.0  climax / peak: the pressure the arc was building toward finally breaks. Maximum,
  irreversible stakes.
Pick the band matching the chapter's strongest SUSTAINED pressure, then place it within that band.

## Calibrate across chapters — the hard part
Your scores are compared against the running average of ALL earlier scores, and any chapter sitting
far from that average is flagged as a sag or a spike for the Editor. So a 0.6 in chapter 30 must
mean the same intensity as a 0.6 in chapter 5. Already-scored chapters are listed for you: study
them, re-read one or two in full, and rate the new chapters against them on the SAME scale. Drift
between passes manufactures false alarms about chapters nobody wrote badly — inconsistent scaling
is a real failure, not rounding noise.

## How to work — research first, then score
Read EACH chapter you are scoring IN FULL before scoring it; never score from a title or an
excerpt, because tension lives in the whole arc. Re-read the nearest already-scored chapters as
calibration anchors. Only then emit the structured scores.
Score EXACTLY the chapters you were given: one entry each, chapter_id matching exactly, no more and
no fewer. Every chapter gets a score even when slack — a thin chapter scores LOW, it is never
skipped.

## Pacing label
Choose the single label that fits the chapter's motion: lull, rising, climax, falling, or steady.

## Feed note
After scoring, write one short feed_note in your own voice about the SHAPE you saw this pass — a run
of steady chapters, a spike, a stretch that sags. If the SEQUENCE is going wrong (the same beat
shape repeating chapter after chapter, or a thread resolving before it earned its payoff), name it
here as an observation for the team. Stay on the curve and the shape; do not critique individual
sentences or ask for revisions — that is the Editor's lane.""" + DECISIVENESS_NOTE

_BATCH_SIZE = 5
_READINESS_DIVISOR = 3
# Enough of the recent curve to hold a scale against, without replaying every
# score the story has ever had.
_CALIBRATION_ANCHORS = 5


class StructureAnalyst(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 180,
        personality: str = "",
        pull_mode: bool = False,
        advisory_token_budget: int = 2000,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="structure_analyst", personality=personality)
        self.pull_mode = pull_mode
        self._advisory_token_budget = advisory_token_budget

    async def _unscored_recent_chapters(self) -> list:
        chapters = await self._read.list_chapters()
        scored_ids = {s.chapter_id for s in await self._read.list_structure_scores()}
        unscored = [c for c in chapters if c.id not in scored_ids]
        return unscored[-_BATCH_SIZE:]

    async def readiness(self) -> float:
        unscored = await self._unscored_recent_chapters()
        if not unscored:
            return 0.0
        return min(1.0, len(unscored) / _READINESS_DIVISOR)

    async def poll(self) -> dict:
        return {
            "unscored": await self._unscored_recent_chapters(),
            "scores": await self._read.list_structure_scores(),
            "chapters": await self._read.list_chapters(),
            "summaries": await self._read.list_chapter_summaries(),
        }

    def _calibration_note(self, ctx: dict) -> str:
        """The recent scored curve, so this pass rates against the same scale
        the earlier passes used. Empty on a first pass -- nothing to anchor to."""
        scores = ctx.get("scores") or []
        if not scores:
            return ""
        titles = {c.id: c.title for c in ctx.get("chapters", [])}
        recent = scores[-_CALIBRATION_ANCHORS:]
        lines = "\n".join(
            f"- '{titles.get(s.chapter_id, s.chapter_id)}' (id:{s.chapter_id}): "
            f"tension {s.tension} · {s.pacing_label}"
            for s in recent
        )
        return (
            "\n\nAlready scored — rate the new chapters on this same scale:\n" + lines
        )

    async def work(self, ctx: dict) -> StructureAnalystOutput | None:
        chapters = ctx["unscored"]
        if not chapters:
            return None
        if self.pull_mode:
            # Tension is a property of the whole arc, so an excerpt is the wrong
            # unit entirely -- a tooled Analyst reads the chapters instead.
            listing = "\n".join(f"- Chapter id:{c.id} '{c.title}'" for c in chapters)
        else:
            summaries_by_id = {s.chapter_id: s.summary for s in ctx["summaries"]}
            entries = [
                AdvisoryEntry(label=f"Chapter id:{c.id} '{c.title}'", summary=summaries_by_id.get(c.id), verbatim=c.prose)
                for c in chapters
            ]
            listing = assemble_advisory(entries, self._advisory_token_budget)
        cast = self._guarded_line("In character", self.personality)
        calibration = self._calibration_note(ctx)
        rejections = await self._own_rejections_note()
        msg = f"Score these chapters:\n{listing}{calibration}{rejections}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: StructureAnalystOutput | None, ctx: dict) -> None:
        if out is None:
            return
        valid_ids = {c.id for c in ctx["unscored"]}
        for score in out.scores:
            if score.chapter_id not in valid_ids:
                logger.warning(
                    "structure_analyst: dropped score for unrequested chapter id %r", score.chapter_id
                )
                continue
            payload = AnnotationStructureScored(
                chapter_id=score.chapter_id, tension=score.tension, pacing_label=score.pacing_label
            )
            await self._committer.commit(self.name, EventType.ANNOTATION_STRUCTURE_SCORED, score.chapter_id, payload)
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

    async def _run(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_structure_analyst_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
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
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + SPEECH_MARKER_NOTE + OUTPUT_CONVENTIONS_NOTE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=StructureAnalystOutput,
            backend=backend, tools=tools, skills=CRAFT_SKILLS, subagents=subagents,
            middleware=[tool_call_budget(),
                        ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=StructureAnalystOutput)


from novelizer.agents.registry_types import AgentContext, AgentSpec, AgentTier, ToolGrant, SubagentGrant


def _construct(ctx: AgentContext) -> StructureAnalyst:
    enabled = ctx.settings.structure_analyst_tools_enabled
    subagent_enabled = ctx.settings.structure_analyst_subagent_enabled
    builder = ctx.tooled(build_structure_analyst_runner, enabled, subagent_enabled, "structure_analyst")
    runner = ctx.runner_for("structure_analyst", builder)
    return StructureAnalyst(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.structure_analyst_interval,
        personality=ctx.personalities.get("structure_analyst", ""),
        pull_mode=enabled,
        advisory_token_budget=ctx.settings.advisory_token_budget,
    )


SPEC = AgentSpec(
    name="structure_analyst",
    tool_grant=ToolGrant(enabled_setting="structure_analyst_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="structure_analyst_subagent_enabled"),
    construct=_construct,
    tier=AgentTier.FULL,
    rebuild_on=("agent_temperature",),
)
