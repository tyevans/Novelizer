from __future__ import annotations
from pydantic import BaseModel

_FULL_KNOWLEDGE = frozenset({"plant", "learn", "reveal", "uses"})
_NO_KNOWLEDGE: frozenset[str] = frozenset()


class ChatPersona(BaseModel):
    """Per-agent chat configuration: how the agent presents itself in
    conversation and which intent families it may commit from chat.
    Permissions mirror what the agent may do autonomously — chat is not a
    privilege-escalation path."""

    model_config = {"frozen": True}

    role_prompt: str
    allow_threads: bool = False
    allow_themes: bool = False
    allow_causal: bool = False
    knowledge_actions: frozenset[str] = _NO_KNOWLEDGE


CHAT_PERSONAS: dict[str, ChatPersona] = {
    "author": ChatPersona(
        role_prompt="You are the Author — you write the chapters. You think in scenes, beats, and consequences.",
        allow_threads=True, allow_themes=True, allow_causal=True, knowledge_actions=_FULL_KNOWLEDGE,
    ),
    "editor": ChatPersona(
        role_prompt="You are the Editor — you review chapters for quality, pacing, and voice.",
        allow_threads=True, allow_themes=True, allow_causal=True, knowledge_actions=_FULL_KNOWLEDGE,
    ),
    "world_architect": ChatPersona(
        role_prompt="You are the World Architect — you tend the lore, places, systems, and rules of the world.",
    ),
    "character_keeper": ChatPersona(
        role_prompt="You are the Character Keeper — you track every character's arc, traits, and knowledge.",
        knowledge_actions=frozenset({"learn"}),
    ),
    "continuity_checker": ChatPersona(
        role_prompt="You are the Continuity Checker — you hunt contradictions, leaks, and drift across the manuscript.",
    ),
    "retconner": ChatPersona(
        role_prompt="You are the Retconner — you resolve approved retcons by amending lore cleanly.",
    ),
    "structure_analyst": ChatPersona(
        role_prompt="You are the Structure Analyst — you read the manuscript's tension curve and pacing.",
    ),
}

_ALIASES = {
    "keeper": "character_keeper",
    "architect": "world_architect",
    "continuity": "continuity_checker",
    "analyst": "structure_analyst",
    "structure": "structure_analyst",
    "retcon": "retconner",
}


def resolve_agent_name(token: str) -> str | None:
    """Resolve an @-mention token to a canonical agent name, or None."""
    key = token.strip().lower()
    if key in CHAT_PERSONAS:
        return key
    return _ALIASES.get(key)
