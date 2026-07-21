from __future__ import annotations

RESEARCHER_SYSTEM_PROMPT = """You are a research subagent dispatched by another agent to
investigate the story canon on its behalf. You have the same read-only tools your dispatcher
has (ls, read_file, grep, glob, search_canon) over the story canon filesystem.

You are working on behalf of the {agent_name} agent. When given a question or a task, use the
fewest tool calls that answer it fully -- read the specific file, grep for the specific term, or
search_canon for the specific meaning; do not browse broadly. When you have enough to answer,
stop calling tools and return a concise, directly-answering summary, citing the exact file paths
and record ids you consulted. Never invent facts absent from what you actually read. If you
cannot find an answer after a reasonable search, say so plainly rather than guessing."""

RESEARCHER_DESCRIPTION = (
    "Dispatch for a delegated canon-read task -- a specific question you want answered by "
    "reading, grepping, or searching the story canon, rather than reading it yourself. Give it "
    "a precise question (e.g. \"does chapter 12 show Mateo mentioning his debt?\"), not a vague "
    "instruction to browse."
)


def build_researcher_subagent(agent_name: str, extra_instructions: str = "") -> dict:
    """Build the shared 'researcher' SubAgent spec for a tooled agent to dispatch.

    No `tools` or `model` key is set: omitting both means deepagents' create_deep_agent
    inherits the parent's canon-read toolkit and model automatically (Design Decisions 3-4)."""
    system_prompt = RESEARCHER_SYSTEM_PROMPT.format(agent_name=agent_name) + extra_instructions
    return {
        "name": "researcher",
        "description": RESEARCHER_DESCRIPTION,
        "system_prompt": system_prompt,
    }
