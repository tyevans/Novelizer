from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import RetconAmendments
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import WorldEntry, RetconStatus

SYSTEM_PROMPT = """You are the Retconner for a living fictional world. You receive a contradiction report
and the conflicting world entries. Propose amended versions of the conflicting entries that resolve the
contradiction. Return amended_entries, each with a title, revised body, domain, tags, and supersedes_id
set to the id of the entry it replaces. Only include entries that need to change."""


class Retconner(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="retconner", personality=personality)
        # Requests that failed an attempt (exception or empty response) are
        # deferred so one poisoned request can't block the whole queue.
        self._deferred: set[str] = set()

    async def readiness(self) -> float:
        open_retcons = len(await self._read.list_retcon_requests(status=RetconStatus.open))
        return min(1.0, open_retcons / 3)

    async def poll(self) -> dict:
        open_reqs = await self._read.list_retcon_requests(status=RetconStatus.open)
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
        conflicting = [e for e in ctx["world"] if e.id in req.conflicting_entry_ids]
        text = "\n".join(f"[{e.id}] {e.title}: {e.body}" for e in conflicting) or "(entries not found)"
        cast = self._guarded_line("In character", self.personality)
        msg = f"Contradiction: {req.description}\n\nProposed resolution: {req.proposed_resolution}\n\nConflicting entries:\n{text}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: RetconAmendments | None, ctx: dict) -> None:
        req = ctx["target"]
        if req is None or out is None:
            return
        for e in out.amended_entries:
            entry = WorldEntry(title=e.title, body=e.body, domain=e.domain, tags=e.tags, supersedes_id=e.supersedes_id)
            await self._committer.commit(self.name, EventType.WORLD_ENTRY_SUPERSEDED, entry.id, entry)
        resolved = req.model_copy(update={"status": RetconStatus.resolved, "resolved_by": self.name})
        await self._committer.commit(self.name, EventType.RETCON_REQUEST_RESOLVED, req.id, resolved)
        await self._remark(out.feed_note)

    async def run_once(self) -> None:
        ctx = await self.poll()
        req = ctx["target"]
        if req is None:
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


def build_retconner_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=RetconAmendments)
