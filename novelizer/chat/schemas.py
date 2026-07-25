from __future__ import annotations
from pydantic import BaseModel, Field
from novelizer.agents.schemas import (
    ThreadIntent, SecretPlant, SecretCitation, CausalIntent, ThemeIntent,
)


class ChatReply(BaseModel):
    """Structured output for one chat reply: in-character prose plus optional
    intents. Intents are validated and permission-filtered by ChatService
    against the agent's ChatPersona before any commit."""

    reply_text: str
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    secret_plants: list[SecretPlant] = Field(default_factory=list)
    secret_citations: list[SecretCitation] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)
    theme_intents: list[ThemeIntent] = Field(default_factory=list)
