from __future__ import annotations
from novelizer.agents.author import RETRIEVAL_NOTE
from novelizer.agents.base import GRAPH_RECURSION_LIMIT
from novelizer.chat.personas import CHAT_PERSONAS
from novelizer.chat.schemas import ChatReply

CHAT_SYSTEM_PROMPT = """{role_prompt}
You are in a private consultation with the Director of this living fictional world.
Answer the Director's latest message in character: concrete, specific to this story, and brief.
You may optionally declare intents (threads, secrets, causal edges, themes) when the
conversation genuinely warrants a real change to the story record; otherwise leave every
intent list empty. Never invent ids — cite ids shown in the story context, or use the
minting action (plant/introduce) with a name."""

CHAT_SKILLS = [
    "/skills/outlining", "/skills/promise-payoff", "/skills/pacing",
    "/skills/scene-sequel", "/skills/character-arcs",
]


def build_chat_runner(settings, agent_name: str, callbacks=None, backend=None, tools=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    model_name = settings.author_model if agent_name == "author" else settings.agent_model
    model = build_chat_model(
        model_name, settings.llm_base_url, settings.llm_api_key,
        settings.agent_temperature, max_tokens=settings.llm_max_tokens,
        callbacks=None, streaming=callbacks is not None,
    )
    persona = CHAT_PERSONAS[agent_name]
    system_prompt = CHAT_SYSTEM_PROMPT.format(role_prompt=persona.role_prompt)
    if backend is not None:
        system_prompt = system_prompt + RETRIEVAL_NOTE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=ChatReply,
            backend=backend, tools=tools, skills=CHAT_SKILLS,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    graph = create_deep_agent(model=model, system_prompt=system_prompt, response_format=ChatReply)
    if callbacks:
        return graph.with_config({"callbacks": callbacks})
    return graph
