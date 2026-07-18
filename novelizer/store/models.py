from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional
from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Domain(StrEnum):
    physical = "physical"
    social = "social"
    metaphysical = "metaphysical"
    historical = "historical"
    other = "other"


class CanonStatus(StrEnum):
    active = "active"
    superseded = "superseded"
    contested = "contested"


class EditorialStatus(StrEnum):
    draft = "draft"
    reviewed = "reviewed"
    final = "final"


class RetconStatus(StrEnum):
    open = "open"
    resolved = "resolved"
    rejected = "rejected"


class SignalKind(StrEnum):
    seed = "seed"
    focus = "focus"
    override = "override"
    note = "note"


class ThreadState(StrEnum):
    planted = "planted"
    touched = "touched"
    paid_off = "paid_off"
    abandoned = "abandoned"


class WorldEntry(BaseModel):
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    supersedes_id: Optional[str] = None
    domain: Domain = Domain.physical
    title: str
    body: str
    canon_status: CanonStatus = CanonStatus.active
    tags: list[str] = Field(default_factory=list)


class CharacterRelationship(BaseModel):
    target_character_id: str
    description: str


class Character(BaseModel):
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    supersedes_id: Optional[str] = None
    name: str
    aliases: list[str] = Field(default_factory=list)
    traits: str = ""
    motivations: str = ""
    backstory: str = ""
    arc_status: str = ""
    voice: str = ""
    relationships: list[CharacterRelationship] = Field(default_factory=list)
    canon_status: CanonStatus = CanonStatus.active


class ThreadRecord(BaseModel):
    """Read-side row for a plot thread, built and rebuilt by the Projector
    from the thread.* event log (see novelizer/canon/projector.py). Unlike
    Character/Chapter, thread.* events after the first carry only deltas
    (id + note), so this model's fields accumulate state across events
    rather than being replaced wholesale by each event's payload.
    """

    id: str
    name: str
    state: ThreadState = ThreadState.planted
    touch_count: int = 0
    last_note: str = ""
    last_chapter_id: str = ""


class StructureScore(BaseModel):
    """Read-side row for one chapter's narrative-structure score, built by
    the Projector from annotation.structure_scored events (see
    novelizer/canon/projector.py) and consumed by novelizer/brain/sag_spike.py's
    pure detect_sag_spike function and, in M3.3, the Story Shape TUI view.
    """

    chapter_id: str
    tension: float = Field(ge=0.0, le=1.0)
    pacing_label: str = ""


class Event(BaseModel):
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    story_time: str
    title: str
    description: str
    participant_ids: list[str] = Field(default_factory=list)
    location_id: Optional[str] = None
    consequences: str = ""


class Chapter(BaseModel):
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    supersedes_id: Optional[str] = None
    title: str
    prose: str
    event_ids: list[str] = Field(default_factory=list)
    character_ids: list[str] = Field(default_factory=list)
    editorial_status: EditorialStatus = EditorialStatus.draft
    editor_notes: Optional[str] = None


class RetconRequest(BaseModel):
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    description: str
    conflicting_entry_ids: list[str]
    proposed_resolution: str
    status: RetconStatus = RetconStatus.open
    resolved_by: Optional[str] = None


class DirectorSignal(BaseModel):
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    kind: SignalKind
    body: str
    target_agent: Optional[str] = None
    consumed: bool = False
