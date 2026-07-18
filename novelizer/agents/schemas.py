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


class KnowledgeIntent(BaseModel):
    """One agent-declared secret-knowledge action from structured output.

    `plant` mints a new secret from a freeform `title` (the system slugs it
    into an id -- see novelizer.canon.secrets.slugify_secret_name); `learn`,
    `reveal`, and `uses` must cite an existing secret's `id` rather than
    inventing one. `learn`/`uses` additionally require `character_id` (the
    character who learns/uses the secret); `reveal` and `plant` leave it
    blank. `BaseAgent._commit_knowledge_intents` turns validated intents
    into secret.* commits (see novelizer/agents/base.py). CharacterKeeper is
    restricted to `learn` only (Locked decision #1) -- minting/revealing a
    secret is a narrative-authoring act reserved for Author/Editor.
    """

    action: Literal["plant", "learn", "reveal", "uses"]
    title: str = ""
    id: str = ""
    character_id: str = ""
    note: str = ""


class CausalIntent(BaseModel):
    """One agent-declared causal-edge claim from structured output.

    An edge has no minted identity and no lifecycle (Locked decision #4):
    `cause_chapter_id`/`effect_chapter_id` must each cite an existing
    chapter id. `BaseAgent._commit_causal_intents` drops self-edges
    (cause == effect) and edges citing an unknown chapter id, with a logged
    warning; every other declared edge is committed as its own fact, with
    no deduplication (see novelizer/agents/base.py).
    """

    cause_chapter_id: str
    effect_chapter_id: str
    note: str = ""


class RetconDraft(BaseModel):
    description: str
    conflicting_entry_ids: list[str] = Field(default_factory=list)
    proposed_resolution: str = ""


class KeeperOutput(BaseModel):
    updated_characters: list[CharacterUpdate] = Field(default_factory=list)
    retcon_requests: list[RetconDraft] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    feed_note: str = ""


class EditorVerdict(BaseModel):
    verdict: Literal["approve", "revise"] = "approve"
    notes: str = ""
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)


class ContinuityOutput(BaseModel):
    retcon_requests: list[RetconDraft] = Field(default_factory=list)
    feed_note: str = ""


class RetconAmendments(BaseModel):
    amended_entries: list[WorldEntryDraft] = Field(default_factory=list)
    feed_note: str = ""


class ChapterScore(BaseModel):
    chapter_id: str
    tension: float = Field(ge=0.0, le=1.0)
    pacing_label: str = ""


class StructureAnalystOutput(BaseModel):
    scores: list[ChapterScore] = Field(default_factory=list)
    feed_note: str = ""
