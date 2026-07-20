from __future__ import annotations
from novelizer.chat.personas import CHAT_PERSONAS
from novelizer.chat.schemas import ChatReply

CHAT_SYSTEM_PROMPT = """{role_prompt}
You are in a private consultation with the Director of this living fictional world.
Answer the Director's latest message in character: concrete, specific to this story, and brief.
You may optionally declare intents (threads, secrets, causal edges, themes) when the
conversation genuinely warrants a real change to the story record; otherwise leave every
intent list empty. Never invent ids — cite ids shown in the story context, or use the
minting action (plant/introduce) with a name."""


CHAT_RETRIEVAL_NOTE = (
    "\n\nYou have file tools over the story canon (ls, read_file, grep, glob) and "
    "semantic search (search_canon). The chapter list in the story context is an "
    "index — read any chapter or canon file you need in full before answering. Cite ids "
    "exactly as shown in frontmatter or search results."
)


def build_chat_runner(settings, agent_name: str, callbacks=None, backend=None, tools=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    model_name = settings.author_model if agent_name == "author" else settings.agent_model
    # Telemetry callbacks bind graph-scope (with_config), not on the model:
    # tool executions run in the graph's ToolNode under invoke-time config.
    model = build_chat_model(
        model_name, settings.llm_base_url, settings.llm_api_key,
        settings.agent_temperature, max_tokens=settings.llm_max_tokens,
        callbacks=None, streaming=callbacks is not None,
    )
    persona = CHAT_PERSONAS[agent_name]
    if backend is not None:
        graph = create_deep_agent(
            model=model,
            system_prompt=CHAT_SYSTEM_PROMPT.format(role_prompt=persona.role_prompt) + CHAT_RETRIEVAL_NOTE,
            response_format=ChatReply,
            backend=backend, tools=tools,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": 50}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    graph = create_deep_agent(
        model=model,
        system_prompt=CHAT_SYSTEM_PROMPT.format(role_prompt=persona.role_prompt),
        response_format=ChatReply,
    )
    if callbacks:
        return graph.with_config({"callbacks": callbacks})
    return graph
