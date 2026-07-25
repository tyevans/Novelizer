from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import FlagLabel
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, FlagLabeled
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import FlagStatus

logger = logging.getLogger(__name__)

# Titles render as one row-label in the flags/escalations table. A rambling
# title is the "title-dump" failure the prompt guards against; cap it as a
# belt-and-braces backstop so a model that ignores the instruction can never
# push a paragraph into a column meant for a phrase.
MAX_TITLE_CHARS = 80
MAX_SUMMARY_CHARS = 240

SYSTEM_PROMPT = """You label filed flags for a fiction workshop's issue queue — the short line a
reader scans before opening the full flag. For the ONE flag you are shown, return:
- title: a short noun phrase naming the issue, at most ~8 words. No trailing punctuation,
  no restating the whole description, no quotes.
- summary: ONE plain sentence describing the issue for someone skimming the queue.
Work only from the category and description you are given. Never invent specifics, never
propose a fix — you are labelling the flag, not resolving it."""


class FlagLabeler(BaseAgent):
    """A quick post-filing pass over the flag queue: every open flag that no one
    has labelled yet gets a short title and one-sentence summary, re-committed as
    FLAG_CREATED (a full-payload upsert, so the label lands without a new event
    type). Runs regardless of which agent filed the flag, since flags are
    constructed at a dozen sites but all land in the same open queue."""

    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="flaglabeler", personality=personality)

    async def _untitled(self) -> list:
        open_flags = await self._read.list_flags(status=FlagStatus.open)
        return [f for f in open_flags if not f.title]

    async def readiness(self) -> float:
        pending = await self._untitled()
        if not pending:
            return 0.0
        return min(1.0, len(pending) / 3)

    async def poll(self) -> dict:
        return {"pending": await self._untitled()}

    async def work(self, ctx: dict) -> dict:
        results: dict = {}
        for flag in ctx["pending"]:
            label = await self._label(flag)
            if label is not None:
                results[flag.id] = (flag, label)
        return results

    async def _label(self, flag) -> FlagLabel | None:
        cast = self._guarded_line("In character", self.personality)
        msg = f"Flag category: {flag.category}\nDescription: {flag.description}{cast}"
        try:
            result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        except Exception:
            logger.warning("%s: label call raised; will retry next poll", self.name, exc_info=True)
            return None
        out = result.get("structured_response")
        if not isinstance(out, FlagLabel) or not out.title.strip():
            # An empty title would leave the flag a candidate forever, re-billing
            # a call every poll; treat it as a failed pass and retry instead.
            logger.warning("%s: no usable label (%r); will retry next poll", self.name, type(out).__name__)
            return None
        return out

    async def commit(self, results: dict, ctx: dict) -> None:
        for flag_id, (_flag, label) in results.items():
            await self._committer.commit(
                self.name, EventType.FLAG_LABELED, flag_id,
                FlagLabeled(id=flag_id, title=label.title.strip()[:MAX_TITLE_CHARS],
                            summary=label.summary.strip()[:MAX_SUMMARY_CHARS]),
            )

    async def _run(self) -> None:
        ctx = await self.poll()
        if not ctx["pending"]:
            self.note_pass()
            return
        results = await self.work(ctx)
        await self.commit(results, ctx)


def build_flaglabeler_runner(settings, callbacks=None):
    from agent_kit import build_light_model, build_simple_runner
    # The light path, and the clearest case for it in the fleet: labelling is
    # extraction, not composition, and this agent has no tools -- so the
    # deepagents graph it used to run was a state machine wrapped around a
    # single request. A small generation cap keeps the pass quick and the title
    # honest; thinking is off because eight words do not need a reasoning
    # block. See AgentTier.LIGHT.
    model = build_light_model(
        settings.resolved_light_model, settings.llm_base_url, settings.llm_api_key,
        max_tokens=min(200, settings.llm_max_tokens), callbacks=callbacks,
        reasoning=settings.light_reasoning,
    )
    return build_simple_runner(model=model, system_prompt=SYSTEM_PROMPT,
                               response_format=FlagLabel)


from novelizer.agents.registry_types import AgentContext, AgentSpec, AgentTier


def _construct(ctx: AgentContext) -> FlagLabeler:
    runner = ctx.runner_for("flaglabeler", build_flaglabeler_runner)
    return FlagLabeler(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.default_agent_interval,
        personality=ctx.personalities.get("flaglabeler", ""),
    )


SPEC = AgentSpec(
    name="flaglabeler",
    tool_grant=None,
    construct=_construct,
    tier=AgentTier.LIGHT,
    rebuild_on=("light_model", "light_reasoning"),
)
