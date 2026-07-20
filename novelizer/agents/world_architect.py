from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner, DEFAULT_PASS_REMARK, PASS_PROMPT_INSTRUCTION, GRAPH_RECURSION_LIMIT
from novelizer.agents.schemas import WorldEntriesDraft
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.muse.prompts import architect_settings_note
from novelizer.store.models import WorldEntry

SYSTEM_PROMPT = """You are the World Architect for an ever-expanding fictional world.
Generate new lore, geography, factions, history, and cosmology. You receive a summary of
what already exists plus any director seeds; identify thin or unexplored areas and expand them.
Return 1-3 new world entries, each with a title, 2-4 paragraphs of rich body lore, a domain
(one of: physical, social, metaphysical, historical, other), and tags.""" + PASS_PROMPT_INSTRUCTION + """
Never set no_action when director seeds are present — a seed is always your work."""


class WorldArchitect(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="world_architect", personality=personality)

    async def readiness(self) -> float:
        count = len(await self._read.list_world_entries())
        return max(0.2, 1.0 - count / 50)

    async def poll(self) -> dict:
        return {
            "entries": await self._read.list_world_entries(),
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
            "hand": await self._read.get_active_hand(),
        }

    async def work(self, ctx: dict) -> WorldEntriesDraft | None:
        existing = "\n".join(f"- [{e.domain}] {e.title}: {e.body[:100]}" for e in ctx["entries"][:20]) or "The world is empty."
        seeds = "\n".join(f"Director seed: {s.body}" for s in ctx["signals"]) or "None."
        cast = self._guarded_line("In character", self.personality)
        sparks = architect_settings_note(ctx.get("hand"))
        msg = f"Existing world entries:\n{existing}\n\nDirector seeds:\n{seeds}{sparks}{cast}\n\nGenerate new world entries."
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
            await self._remark(draft.feed_note)
        await self._consume_signals(ctx["signals"])

    async def _run(self) -> None:
        ctx = await self.poll()
        draft = await self.work(ctx)
        await self.commit(draft, ctx)


def build_world_architect_runner(settings, callbacks=None, backend=None, tools=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=WorldEntriesDraft,
            backend=backend, tools=tools,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=WorldEntriesDraft)
