from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from agent_kit import GRAPH_RECURSION_LIMIT
from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE
from novelizer.agents.schemas import RetconAmendments
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.canon_fs.skills_route import CRAFT_SKILLS
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.canon.flags import (
    mark_declined, mark_escalated, mark_escalation_cleared, mark_resolved,
    may_clear_escalation, may_decide, should_escalate_after_failure,
)
from novelizer.store.models import WorldEntry, FlagStatus

logger = logging.getLogger(__name__)

# Judgement lane, and this one MUTATES canon. The decisive move here is often a
# decline, so the note frames the four declines as real outcomes rather than
# pushing toward an amendment.
DECISIVENESS_NOTE = """

## One verification, then act or decline
Verification is complete when you have read the current entries and can cite the spans. At that
point you already know which of the four outcomes applies — amend, already_consistent,
cannot_reproduce, out_of_lane — and three of those four are declines. A decline is a correct
result, not a failure to decide: reach for it plainly rather than re-reading toward a resolution
you have already established is not there. The blast-radius grep is the last step of one
amendment, not the opening of a second investigation."""

SYSTEM_PROMPT = """You are the Retconner for a living fictional world — a surgical canon repair
specialist. A sibling agent (Continuity Checker, Character Keeper, or Editor) has filed a
contradiction report against one or more world entries. Your job is to VERIFY the contradiction
still exists in the live canon, and if it does, resolve it with the smallest amendment that removes
it while preserving everything else those entries truthfully assert.

## Your lane
You repair contradictions between WORLD ENTRIES by superseding them. That is the whole job.

## Not your lane — decline rather than force a fix
- You do NOT invent new plot, lore or history. A retcon reconciles what already exists; it never
  adds a story development. If resolving the contradiction would require inventing facts, the
  request is under-specified: set resolution="cannot_reproduce" and say what is missing.
- You do NOT rewrite chapter prose, character sheets, threads, secrets or themes — those belong to
  the Author and the Character Keeper. If the conflicting ids are not world entries, set
  resolution="out_of_lane".
- You do NOT re-litigate style or pacing. Factual and logical contradictions only.

## How to work — VERIFY, then AMEND
1. VERIFY the contradiction reproduces. The report and the entry bodies shown to you were captured
   on an earlier pass and may be STALE: the paradox may already be fixed, or the report may be
   wrong. Use read_file / grep / search_canon to read the CURRENT entries the report names before
   you touch anything. The inlined bodies are a pointer, not ground truth.
   - No longer reproduces in live canon -> resolution="already_consistent", amend nothing.
   - Report incoherent, or names ids you cannot find -> resolution="cannot_reproduce".
2. AMEND minimally. For each entry that genuinely must change, emit one amended version: change
   ONLY the sentences carrying the contradiction, and keep the title, domain, tags and every other
   true statement verbatim. A good amendment is a scalpel, not a rewrite. Set supersedes_id to the
   exact id of the entry you replace — copy it, never invent it.
3. CHECK YOUR BLAST RADIUS before finalizing. An amendment can create a NEW contradiction: grep the
   canon for other mentions of the fact you changed (change "two suns" to "one sun", then grep for
   "sun"). If other entries assert the old fact, amend them in the same pass; if the collision
   spills into prose you cannot touch, decline with resolution="cannot_reproduce" and name it.
   Never leave canon in a worse state than you found it.
4. STOP once you can cite the evidence for your decision, and emit. Grounding is your stopping rule.

## Grounding
Put the spans you actually read into `evidence` — for amendments and declines alike. If you cannot
cite where you verified something, you have not verified it: read first, then emit.

## Voice
Do the analysis under these neutral instructions. Put your personality only in the one-line
feed_note — never in the amendment text, which must read as plain canon.""" + DECISIVENESS_NOTE


class Retconner(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
        pull_mode: bool = False,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="retconner", personality=personality)
        # Requests that failed an attempt (exception or empty response) are
        # deferred so one poisoned request can't block the whole queue.
        self._deferred: set[str] = set()
        self.pull_mode = pull_mode

    async def readiness(self) -> float:
        open_flags = len(await self._read.list_flags(category="contradiction", status=FlagStatus.open))
        return min(1.0, open_flags / 3)

    async def poll(self) -> dict:
        open_reqs = await self._read.list_flags(category="contradiction", status=FlagStatus.open)
        self._deferred &= {r.id for r in open_reqs}
        candidates = [r for r in open_reqs if r.id not in self._deferred]
        if not candidates and open_reqs:
            # Every open request has failed one attempt — start a fresh pass
            # rather than idling forever with readiness pinned at 1.0.
            self._deferred.clear()
            candidates = open_reqs
        return {"target": candidates[0] if candidates else None, "world": await self._read.list_world_entries()}

    async def work(self, ctx: dict) -> RetconAmendments | None:
        req = ctx["target"]
        if req is None:
            return None
        conflicting = [e for e in ctx["world"] if e.id in req.related_entry_ids]
        if self.pull_mode:
            # Ids and titles only: the prompt already declares inlined bodies
            # stale pointers and orders a live re-read, so a tooled Retconner
            # gets the ids to read, not bodies to trust.
            text = "\n".join(f"[{e.id}] {e.title}" for e in conflicting) or "(entries not found)"
        else:
            text = "\n".join(f"[{e.id}] {e.title}: {e.body}" for e in conflicting) or "(entries not found)"
        cast = self._guarded_line("In character", self.personality)
        msg = f"Contradiction: {req.description}\n\nProposed resolution: {req.proposed_resolution}\n\nConflicting entries:\n{text}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def _decline(self, req, resolution: str, reason: str) -> None:
        """Close a request without amending anything. Distinct from resolving
        it: nothing was repaired, and the filing agent's log should say so."""
        logger.info("retconner: declining request %s (%s): %s", req.id, resolution, reason)
        if not may_decide(req):
            return
        rejected = mark_declined(req, by=self.name, resolution=resolution, reason=reason)
        await self._committer.commit(self.name, EventType.FLAG_REJECTED, req.id, rejected)
        if should_escalate_after_failure(rejected):
            await self._committer.commit(self.name, EventType.FLAG_ESCALATED, req.id,
                                         mark_escalated(rejected))

    async def commit(self, out: RetconAmendments | None, ctx: dict) -> None:
        req = ctx["target"]
        if req is None or out is None or not may_decide(req):
            return
        if out.resolution != "amend":
            await self._decline(req, out.resolution, out.reason)
            await self._remark(out.feed_note)
            return
        for e in out.amended_entries:
            entry = WorldEntry(title=e.title, body=e.body, domain=e.domain, tags=e.tags, supersedes_id=e.supersedes_id)
            await self._committer.commit(self.name, EventType.WORLD_ENTRY_SUPERSEDED, entry.id, entry)
        resolved = mark_resolved(req, by=self.name)
        await self._committer.commit(self.name, EventType.FLAG_RESOLVED, req.id, resolved)
        if may_clear_escalation(resolved):
            await self._committer.commit(self.name, EventType.FLAG_ESCALATION_CLEARED, req.id,
                                         mark_escalation_cleared(resolved, by="agent"))
        await self._remark(out.feed_note)

    async def _run(self) -> None:
        ctx = await self.poll()
        req = ctx["target"]
        if req is None:
            return
        # Lane guard, before any LLM call. Voice-drift retcons cite character
        # ids; this agent only supersedes world entries, so amending one would
        # mint an entry that supersedes nothing. An EMPTY id list is not
        # out-of-lane -- the filer may have described the conflict in prose
        # only, and the model can still work from the description.
        named = req.related_entry_ids
        if named and not any(e.id in named for e in ctx["world"]):
            await self._decline(
                req, "out_of_lane",
                "related_entry_ids name no world entry; the Retconner only amends world entries",
            )
            self._deferred.discard(req.id)
            return
        try:
            out = await self.work(ctx)
            if out is None:
                self._deferred.add(req.id)
                return
            await self.commit(out, ctx)
        except Exception:
            self._deferred.add(req.id)
            raise
        self._deferred.discard(req.id)


def build_retconner_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
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
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + OUTPUT_CONVENTIONS_NOTE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=RetconAmendments,
            backend=backend, tools=tools, skills=CRAFT_SKILLS, subagents=subagents,
            middleware=[tool_call_budget(),
                        ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=RetconAmendments)


from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant, SubagentGrant


def _construct(ctx: AgentContext) -> Retconner:
    enabled = ctx.settings.retconner_tools_enabled
    subagent_enabled = ctx.settings.retconner_subagent_enabled
    builder = ctx.tooled(build_retconner_runner, enabled, subagent_enabled, "retconner")
    runner = ctx.runner_for("retconner", builder)
    return Retconner(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.default_agent_interval,
        personality=ctx.personalities.get("retconner", ""),
        pull_mode=enabled,
    )


SPEC = AgentSpec(
    name="retconner",
    tool_grant=ToolGrant(enabled_setting="retconner_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="retconner_subagent_enabled"),
    construct=_construct,
    rebuild_on=("agent_temperature",),
)
