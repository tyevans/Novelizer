from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class WorldEntryDraft(BaseModel):
    title: str
    body: str
    domain: str = "physical"
    tags: list[str] = Field(default_factory=list)
    supersedes_id: Optional[str] = None


class WorldEntriesDraft(BaseModel):
    entries: list[WorldEntryDraft] = Field(default_factory=list)


class CharacterUpdate(BaseModel):
    id: str
    arc_status: Optional[str] = None
    traits: Optional[str] = None
    motivations: Optional[str] = None
    backstory: Optional[str] = None


class RetconDraft(BaseModel):
    description: str
    conflicting_entry_ids: list[str] = Field(default_factory=list)
    proposed_resolution: str = ""


class KeeperOutput(BaseModel):
    updated_characters: list[CharacterUpdate] = Field(default_factory=list)
    retcon_requests: list[RetconDraft] = Field(default_factory=list)


class EditorVerdict(BaseModel):
    verdict: Literal["approve", "revise"] = "approve"
    notes: str = ""


class ContinuityOutput(BaseModel):
    retcon_requests: list[RetconDraft] = Field(default_factory=list)


class RetconAmendments(BaseModel):
    amended_entries: list[WorldEntryDraft] = Field(default_factory=list)
