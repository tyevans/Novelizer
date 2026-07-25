from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from agent_kit import GRAPH_RECURSION_LIMIT
from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE
from novelizer.agents.schemas import CurationDecision
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.canon_fs.skills_route import CRAFT_SKILLS
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, WorldEntryRetired
from novelizer.canon.flags import (
    mark_declined, mark_escalated, mark_escalation_cleared, mark_resolved,
    may_clear_escalation, may_decide, should_escalate_after_failure,
)
from novelizer.store.models import WorldEntry, FlagStatus

logger = logging.getLogger(__name__)

# Categories the Curator owns. Triage routes these to "curator" (see triage.py).
_CURATION_CATEGORIES = ("world_craft", "world_relevance", "world_redundancy", "worldbuilding")

SYSTEM_PROMPT = """You are the Curator for a living fictional world — the editor who keeps the
WORLD ENTRIES (not the story prose) coherent, relevant, and free of clutter. A sibling agent has
filed a curation flag against one or more world entries. Verify the concern still holds in live
canon, then resolve it with the least destructive action that serves the story.

## Your lane
You improve WORLD ENTRIES by superseding or retiring them. That is the whole job. You do NOT touch
chapter prose, character sheets, threads, secrets, or themes. If the flag's ids name no world
entry, set action="reject" and say it's out of your lane.

## Your actions — prefer the least destructive
- revise: the entry stays canon but its prose needs work (bloated, muddled, weak). Return the
  improved entry in `entry`, keeping title/domain/tags unless the flag is about those. Set
  entry.supersedes_id to the exact id you are replacing.
- reclassify: the entry's facts are fine but its domain/tags are wrong, so it surfaces in the
  wrong places. Return the same body with corrected domain/tags in `entry`, supersedes_id set.
- merge: two or more entries overlap. Return ONE consolidated entry in `entry` (supersedes_id =
  the primary you keep) and list the OTHER entries' ids in `retire_ids`.
- retire: the entry no longer serves the story and has no better home. List its id in
  `retire_ids`. Retire is the LAST RESORT — when in doubt, revise, reclassify, or merge instead.
  Never retire an entry that current chapters clearly rely on.
- reject: the flag is stale, wrong, or out of lane. Give a one-line `reason`.

## How to work — VERIFY, then ACT
The flag and the inlined bodies were captured on an earlier pass and may be STALE. Use
read_file / grep / search_canon to read the CURRENT entries before you act. Put the spans you
actually read into `evidence`. If you cannot cite where you verified something, you have not
verified it: read first, then emit.

## Voice
Do the analysis under these neutral instructions. Put your personality only in the one-line
feed_note — never in entry bodies, which must read as plain canon."""


class Curator(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
        pull_mode: bool = False,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="curator", personality=personality)
        self._deferred: set[str] = set()
        self.pull_mode = pull_mode

    async def _open_curation_flags(self) -> list:
        flags = await self._read.list_flags(status=FlagStatus.open)
        return [f for f in flags if f.category in _CURATION_CATEGORIES]

    async def readiness(self) -> float:
        return min(1.0, len(await self._open_curation_flags()) / 3)

    async def poll(self) -> dict:
        open_flags = await self._open_curation_flags()
        self._deferred &= {f.id for f in open_flags}
        candidates = [f for f in open_flags if f.id not in self._deferred]
        if not candidates and open_flags:
            self._deferred.clear()
            candidates = open_flags
        return {"target": candidates[0] if candidates else None,
                "world": await self._read.list_world_entries()}

    async def work(self, ctx: dict) -> CurationDecision | None:
        flag = ctx["target"]
        if flag is None:
            return None
        related = [e for e in ctx["world"] if e.id in flag.related_entry_ids]
        if self.pull_mode:
            text = "\n".join(f"[{e.id}] {e.title}" for e in related) or "(entries not found)"
        else:
            text = "\n".join(f"[{e.id}] {e.title}: {e.body}" for e in related) or "(entries not found)"
        cast = self._guarded_line("In character", self.personality)
        msg = (f"Curation flag [{flag.category}]: {flag.description}\n\n"
               f"Proposed resolution: {flag.proposed_resolution}\n\nRelated entries:\n{text}{cast}")
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def _decline(self, flag, resolution: str, reason: str) -> None:
        logger.info("curator: declining flag %s (%s): %s", flag.id, resolution, reason)
        if not may_decide(flag):
            return
        rejected = mark_declined(flag, by=self.name, resolution=resolution, reason=reason)
        await self._committer.commit(self.name, EventType.FLAG_REJECTED, flag.id, rejected)
        if should_escalate_after_failure(rejected):
            await self._committer.commit(self.name, EventType.FLAG_ESCALATED, flag.id,
                                         mark_escalated(rejected))

    async def commit(self, out: CurationDecision | None, ctx: dict) -> None:
        flag = ctx["target"]
        if flag is None or out is None or not may_decide(flag):
            return
        # Validate the decision's shape; a malformed action declines rather than
        # committing a half-formed mutation.
        if out.action == "reject":
            await self._decline(flag, "reject", out.reason)
            await self._remark(out.feed_note)
            return
        if out.action in ("revise", "reclassify", "merge") and out.entry is None:
            await self._decline(flag, "invalid", f"{out.action} requires an entry")
            await self._remark(out.feed_note)
            return
        if out.action in ("merge", "retire") and not out.retire_ids:
            await self._decline(flag, "invalid", f"{out.action} requires retire_ids")
            await self._remark(out.feed_note)
            return

        if out.action in ("revise", "reclassify", "merge"):
            e = out.entry
            entry = WorldEntry(title=e.title, body=e.body, domain=e.domain, tags=e.tags,
                               supersedes_id=e.supersedes_id)
            await self._committer.commit(self.name, EventType.WORLD_ENTRY_SUPERSEDED, entry.id, entry)
        if out.action in ("merge", "retire"):
            for rid in out.retire_ids:
                payload = WorldEntryRetired(entry_id=rid, reason=out.reason, flag_id=flag.id)
                await self._committer.commit(self.name, EventType.WORLD_ENTRY_RETIRED, rid, payload)

        resolved = mark_resolved(flag, by=self.name)
        await self._committer.commit(self.name, EventType.FLAG_RESOLVED, flag.id, resolved)
        if may_clear_escalation(resolved):
            await self._committer.commit(self.name, EventType.FLAG_ESCALATION_CLEARED, flag.id,
                                         mark_escalation_cleared(resolved, by="agent"))
        await self._remark(out.feed_note)

    async def _run(self) -> None:
        ctx = await self.poll()
        flag = ctx["target"]
        if flag is None:
            return
        # Lane guard, before any LLM call: the Curator only mutates world
        # entries, so a flag naming no active world entry cannot be actioned.
        # An EMPTY id list is not out-of-lane — the filer may have described
        # the target in prose only.
        named = flag.related_entry_ids
        if named and not any(e.id in named for e in ctx["world"]):
            await self._decline(
                flag, "out_of_lane",
                "related_entry_ids name no world entry; the Curator only curates world entries",
            )
            self._deferred.discard(flag.id)
            return
        try:
            out = await self.work(ctx)
            if out is None:
                self._deferred.add(flag.id)
                return
            await self.commit(out, ctx)
        except Exception:
            self._deferred.add(flag.id)
            raise
        self._deferred.discard(flag.id)


def build_curator_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
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
            model=model, system_prompt=system_prompt, response_format=CurationDecision,
            backend=backend, tools=tools, skills=CRAFT_SKILLS, subagents=subagents,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=CurationDecision)


from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant, SubagentGrant


def _construct(ctx: AgentContext) -> Curator:
    enabled = ctx.settings.curator_tools_enabled
    subagent_enabled = ctx.settings.curator_subagent_enabled
    builder = ctx.tooled(build_curator_runner, enabled, subagent_enabled, "curator")
    runner = ctx.runner_for("curator", builder)
    return Curator(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.default_agent_interval,
        personality=ctx.personalities.get("curator", ""),
        pull_mode=enabled,
    )


SPEC = AgentSpec(
    name="curator",
    tool_grant=ToolGrant(enabled_setting="curator_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="curator_subagent_enabled"),
    construct=_construct,
    rebuild_on=("agent_temperature",),
)
