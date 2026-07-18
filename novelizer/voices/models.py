from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class ProseProfile(BaseModel):
    """A natural-language casting note describing a prose voice.

    Deliberately not a parameter DSL: `casting_note` is prose a human wrote,
    handed to the Author/Editor verbatim at work-time.
    """

    name: str
    casting_note: str


class VoicePack(BaseModel):
    """A voice pack: prose profiles the Author can be cast in, plus
    per-agent personality casting notes (consumed starting M2.2).
    """

    name: str
    prose_profiles: dict[str, ProseProfile] = Field(default_factory=dict)
    agent_personalities: dict[str, str] = Field(default_factory=dict)

    def profile(self, name: str) -> Optional[ProseProfile]:
        return self.prose_profiles.get(name)
