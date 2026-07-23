from __future__ import annotations
from langchain_core.tools import tool
from novelizer.agents.base import GRAPH_RECURSION_LIMIT
from novelizer.research.schemas import ResearchAnswer
from novelizer.research.tools import (
    check_beat_drift, check_completion, check_leaks, check_paradoxes,
    check_promise_ledger, check_stale_threads,
)

RESEARCH_SYSTEM_PROMPT = """## Role
You are a research analyst for this story's canon. The Director is asking you
questions about the project — answer precisely, cite chapter/thread/secret/
promise ids you find rather than describing things vaguely, and use the
diagnostic tools when a question calls for actually checking something (e.g.
"are there any leaked secrets?", "is anything overdue?") rather than just
narrating what you already know. You never modify canon: you have no tools
that write, and you never propose changes — you only answer."""


def _make_diagnostic_tools(read_store):
    @tool("check_stale_threads")
    async def check_stale_threads_tool() -> str:
        """Check for story threads that have gone stale (no touch in
        several chapters and not yet paid off or abandoned)."""
        return await check_stale_threads(read_store)

    @tool("check_leaks")
    async def check_leaks_tool() -> str:
        """Check for secret leaks: a character referencing a secret they
        haven't learned or that hasn't been revealed."""
        return await check_leaks(read_store)

    @tool("check_paradoxes")
    async def check_paradoxes_tool() -> str:
        """Check the causal graph for ordering violations or cycles."""
        return await check_paradoxes(read_store)

    @tool("check_promise_ledger")
    async def check_promise_ledger_tool() -> str:
        """Check for promises (Chekhov's guns, foreshadowing) that are
        overdue or due for payoff."""
        return await check_promise_ledger(read_store)

    @tool("check_beat_drift")
    async def check_beat_drift_tool() -> str:
        """Check whether the adopted blueprint's beats are landing inside
        their expected chapter windows."""
        return await check_beat_drift(read_store)

    @tool("check_completion")
    async def check_completion_tool() -> str:
        """Check whether the adopted blueprint's shape is fully realized
        (every beat fulfilled, every promise settled, every arc resolved)."""
        return await check_completion(read_store)

    return [
        check_stale_threads_tool, check_leaks_tool, check_paradoxes_tool,
        check_promise_ledger_tool, check_beat_drift_tool, check_completion_tool,
    ]


def build_research_runner(settings, callbacks=None, backend=None, tools=None, read_store=None):
    from deepagents import create_deep_agent
    from agent_kit import build_chat_model
    from agent_kit import ExcludeToolsMiddleware

    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        settings.agent_temperature, max_tokens=settings.llm_max_tokens,
        callbacks=None, streaming=callbacks is not None,
    )
    if backend is not None:
        all_tools = list(tools or []) + _make_diagnostic_tools(read_store)
        graph = create_deep_agent(
            model=model, system_prompt=RESEARCH_SYSTEM_PROMPT, response_format=ResearchAnswer,
            backend=backend, tools=all_tools,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    graph = create_deep_agent(model=model, system_prompt=RESEARCH_SYSTEM_PROMPT, response_format=ResearchAnswer)
    if callbacks:
        return graph.with_config({"callbacks": callbacks})
    return graph
