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
    feed_note: str = ""


class CharacterUpdate(BaseModel):
    id: str
    arc_status: Optional[str] = None
    traits: Optional[str] = None
    motivations: Optional[str] = None
    backstory: Optional[str] = None
    voice: Optional[str] = None


class ThreadIntent(BaseModel):
    """One agent-declared plot-thread action from structured output.

    `plant` mints a new thread from a freeform `name` (the system slugs it
    into an id — see novelizer.canon.threads.slugify_thread_name); `touch`,
    `pay_off`, and `abandon` must cite an existing thread's `id` rather than
    inventing one. `BaseAgent._commit_thread_intents` turns validated
    intents into thread.* commits (see novelizer/agents/base.py).
    """

    action: Literal["plant", "touch", "pay_off", "abandon"]
    name: str = ""
    id: str = ""
    note: str = ""


class RetconDraft(BaseModel):
    description: str
    conflicting_entry_ids: list[str] = Field(default_factory=list)
    proposed_resolution: str = ""


class KeeperOutput(BaseModel):
    updated_characters: list[CharacterUpdate] = Field(default_factory=list)
    retcon_requests: list[RetconDraft] = Field(default_factory=list)
    feed_note: str = ""


class EditorVerdict(BaseModel):
    verdict: Literal["approve", "revise"] = "approve"
    notes: str = ""
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)


class ContinuityOutput(BaseModel):
    retcon_requests: list[RetconDraft] = Field(default_factory=list)
    feed_note: str = ""


class RetconAmendments(BaseModel):
    amended_entries: list[WorldEntryDraft] = Field(default_factory=list)
    feed_note: str = ""
