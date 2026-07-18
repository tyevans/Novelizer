from __future__ import annotations
from typing import Protocol
from pydantic import BaseModel, Field


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)


class Runner(Protocol):
    async def ainvoke(self, inputs: dict) -> dict: ...
