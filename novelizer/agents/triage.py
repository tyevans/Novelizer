from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner, GRAPH_RECURSION_LIMIT
from novelizer.agents.schemas import TriageVerdict
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import FlagStatus

logger = logging.getLogger(__name__)

# category -> agent name that already polls list_flags(category=..., status=open)
# for its own resolution work, same pattern Retconner uses for "contradiction".
# Unmapped categories are Triage's own catch-all responsibility.
_CATEGORY_OWNERS: dict[str, str] = {
    "contradiction": "retconner",
    "pacing": "structure_analyst",
    "worldbuilding": "world_architect",
    "thematic": "plotter",
    "voice_drift": "retconner",
}

DEFAULT_STALE_AFTER = 5

SYSTEM_PROMPT = """You are Triage for a living fictional world — the one agent that reads
every flag any other agent raises, regardless of category, and decides whether it's a real
issue worth keeping open.

## Your lane
For the ONE flag you're shown: decide "real" or "dismiss". You never edit canon and you never
invent a fix — that's the owning agent's job once the flag is confirmed. Your only output is a
verdict, an optional reason, and (only for a flag whose category has no known owner) an optional
`reclassify_category` if you can tell what it actually is from a fixed vocabulary the owning
agents understand: contradiction, pacing, worldbuilding, thematic, voice_drift. If none fit,
leave `reclassify_category` blank — it stays a catch-all and ages toward stale.

## How to work
1. VERIFY: read the flag's description and cited entries. Is this still a real, current issue,
   or has canon already moved past it / was it never actually a problem?
2. DECIDE: "real" keeps it open. "dismiss" closes it — use this for stale, duplicate-in-substance,
   or simply wrong flags. Give a one-line `reason` either way; it goes in the log.
3. Every "real" verdict also gets a `severity`: "critical" if it contradicts a resolved arc,
   breaks a paid-off thread, or spans multiple already-written chapters; "major" if it affects the
   current chapter's coherence; "minor" otherwise. A "critical" call escalates the flag immediately,
   so reserve it for issues that can't wait for the owning agent's normal poll.
4. STOP once you can state the evidence for your verdict.

## Voice
Neutral. Put personality only in `feed_note`, never in `reason`."""


class Triage(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
        stale_after: int = DEFAULT_STALE_AFTER,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="triage", personality=personality)
        self._deferred: set[str] = set()
        self._stale_after = stale_after

    async def readiness(self) -> float:
        open_flags = len(await self._read.list_flags(status=FlagStatus.open))
        return min(1.0, open_flags / 3)

    async def poll(self) -> dict:
        open_flags = await self._read.list_flags(status=FlagStatus.open)
        self._deferred &= {f.id for f in open_flags}
        candidates = [f for f in open_flags if f.id not in self._deferred]
        if not candidates and open_flags:
            self._deferred.clear()
            candidates = open_flags
        return {"target": candidates[0] if candidates else None}

    async def work(self, ctx: dict) -> TriageVerdict | None:
        flag = ctx["target"]
        if flag is None:
            return None
        cast = self._guarded_line("In character", self.personality)
        msg = (
            f"Flag category: {flag.category}\nDescription: {flag.description}\n"
            f"Related entry ids: {flag.related_entry_ids}\n"
            f"Proposed resolution: {flag.proposed_resolution}{cast}"
        )
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: TriageVerdict | None, ctx: dict) -> None:
        flag = ctx["target"]
        if flag is None or out is None:
            return
        if out.verdict == "dismiss":
            rejected = flag.model_copy(update={"status": FlagStatus.rejected, "resolved_by": self.name})
            await self._committer.commit(self.name, EventType.FLAG_REJECTED, flag.id, rejected)
            await self._remark(out.feed_note)
            return
        flag = flag.model_copy(update={"severity": out.severity})
        if out.severity == "critical" and not flag.escalated:
            flag = flag.model_copy(update={"escalated": True})
            await self._committer.commit(self.name, EventType.FLAG_ESCALATED, flag.id, flag)
        owner = _CATEGORY_OWNERS.get(flag.category)
        if owner is not None:
            # Owned and verified real: persist the assessed severity (and
            # any escalation above) and leave it open for the owner's own
            # poll to pick up next cycle.
            await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
            await self._remark(out.feed_note)
            return
        # Unowned catch-all: reclassify if Triage recognized it, else age
        # the pass counter toward stale so it doesn't loop forever.
        new_category = out.reclassify_category or flag.category
        if out.reclassify_category and out.reclassify_category in _CATEGORY_OWNERS:
            reclassified = flag.model_copy(update={"category": new_category})
            await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, reclassified)
            await self._remark(out.feed_note)
            return
        passes = flag.triage_passes + 1
        if passes >= self._stale_after:
            aged = flag.model_copy(update={"triage_passes": passes, "status": FlagStatus.stale})
            await self._committer.commit(self.name, EventType.FLAG_REJECTED, flag.id, aged)
        else:
            aged = flag.model_copy(update={"triage_passes": passes})
            await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, aged)
        await self._remark(out.feed_note)

    async def _run(self) -> None:
        ctx = await self.poll()
        flag = ctx["target"]
        if flag is None:
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


def build_triage_runner(settings, callbacks=None, backend=None, tools=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        graph = create_deep_agent(
            model=model, system_prompt=SYSTEM_PROMPT, response_format=TriageVerdict,
            backend=backend, tools=tools,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=TriageVerdict)


from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


def _construct(ctx: AgentContext) -> Triage:
    enabled = ctx.settings.triage_tools_enabled
    builder = ctx.tooled(build_triage_runner, enabled)
    runner = ctx.runner_for("triage", builder)
    return Triage(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.triage_interval,
        personality=ctx.personalities.get("triage", ""),
    )


SPEC = AgentSpec(
    name="triage",
    tool_grant=ToolGrant(enabled_setting="triage_tools_enabled"),
    construct=_construct,
)
