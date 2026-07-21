from __future__ import annotations
from pydantic import BaseModel


class ResearchAnswer(BaseModel):
    """The research agent's reply to a single Director question. Read-only
    by construction — this schema carries no intents, no proposal fields;
    the research context never writes to canon."""

    answer_text: str
