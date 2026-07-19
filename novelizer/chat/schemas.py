from __future__ import annotations
from pydantic import BaseModel, Field
from novelizer.agents.schemas import ThreadIntent, KnowledgeIntent, CausalIntent, ThemeIntent


class ChatReply(BaseModel):
    """Structured output for one chat reply: in-character prose plus optional
    intents. Intents are validated and permission-filtered by ChatService
    against the agent's ChatPersona before any commit."""

    reply_text: str
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)
    theme_intents: list[ThemeIntent] = Field(default_factory=list)
