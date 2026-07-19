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


def build_chat_runner(settings, agent_name: str):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model_name = settings.author_model if agent_name == "author" else settings.agent_model
    model = build_chat_model(
        model_name, settings.llm_base_url, settings.llm_api_key,
        settings.agent_temperature, max_tokens=settings.llm_max_tokens,
    )
    persona = CHAT_PERSONAS[agent_name]
    return create_deep_agent(
        model=model,
        system_prompt=CHAT_SYSTEM_PROMPT.format(role_prompt=persona.role_prompt),
        response_format=ChatReply,
    )
