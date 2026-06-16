from __future__ import annotations
import json
from novelizer.agents.base import BaseAgent, AgentState
from novelizer.store.models import WorldEntry, RetconStatus
from novelizer.store.queries import Store

SYSTEM_PROMPT = """You are the Retconner for a living fictional world.
You receive a contradiction report and the conflicting world entries.
Your job: propose and write amended versions of the conflicting entries to resolve the contradiction.

Respond with JSON:
  "amended_entries": list of world entry objects, each with:
    (title, body, domain, tags, supersedes_id pointing to the entry being replaced)

Only include entries that need to change. Respond with ONLY the JSON object."""


class Retconner(BaseAgent):
    def __init__(self, store: Store, min_interval: int = 120, llm_model: str = "llama3.2") -> None:
        super().__init__(name="retconner", store=store, min_interval=min_interval, llm_model=llm_model)

    async def readiness_check(self) -> float:
        open_retcons = await self.store.db.count_open_retcons()
        return min(1.0, open_retcons / 3)

    async def poll(self, state: AgentState) -> None:
        reqs = await self.store.list_retcon_requests(status=RetconStatus.open)
        state.context["target_retcon"] = reqs[0] if reqs else None
        state.context["world_entries"] = await self.store.list_world_entries()

    async def work(self, state: AgentState) -> None:
        req = state.context.get("target_retcon")
        if not req:
            state.context["amended_entries"] = []
            return

        all_entries = state.context["world_entries"]
        conflicting = [e for e in all_entries if e.id in req.conflicting_entry_ids]
        conflict_text = "\n".join(f"[{e.id}] {e.title}: {e.body}" for e in conflicting)

        user_msg = (
            f"Contradiction: {req.description}\n\n"
            f"Proposed resolution: {req.proposed_resolution}\n\n"
            f"Conflicting entries:\n{conflict_text}"
        )
        raw = await self._llm([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ])
        try:
            data = json.loads(raw)
            state.context["amended_entries"] = [WorldEntry(**e) for e in data.get("amended_entries", [])]
        except (json.JSONDecodeError, TypeError, ValueError):
            state.context["amended_entries"] = []

    async def commit(self, state: AgentState) -> None:
        req = state.context.get("target_retcon")
        if not req:
            return
        for entry in state.context.get("amended_entries", []):
            if entry.supersedes_id:
                await self.store.supersede_world_entry(entry.supersedes_id, entry)
            else:
                await self.store.save_world_entry(entry)
        await self.store.resolve_retcon(req.id, self.name)
