from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class EventType:
    WORLD_ENTRY_CREATED = "world_entry.created"
    WORLD_ENTRY_SUPERSEDED = "world_entry.superseded"
    CHARACTER_CREATED = "character.created"
    CHARACTER_UPDATED = "character.updated"
    CHAPTER_CREATED = "chapter.created"
    CHAPTER_STATUS_CHANGED = "chapter.status_changed"
    DIRECTOR_SIGNAL_CREATED = "director_signal.created"
    DIRECTOR_SIGNAL_CONSUMED = "director_signal.consumed"
    RETCON_REQUEST_CREATED = "retcon_request.created"
    RETCON_REQUEST_RESOLVED = "retcon_request.resolved"
    RETCON_REQUEST_REJECTED = "retcon_request.rejected"
    PROPOSAL_CREATED = "proposal.created"
    PROPOSAL_APPROVED = "proposal.approved"
    PROPOSAL_REJECTED = "proposal.rejected"
    AUTONOMY_CHANGED = "autonomy.changed"
    AGENT_REMARKED = "agent.remarked"
    THREAD_PLANTED = "thread.planted"
    THREAD_TOUCHED = "thread.touched"
    THREAD_PAID_OFF = "thread.paid_off"
    THREAD_ABANDONED = "thread.abandoned"
    ANNOTATION_STRUCTURE_SCORED = "annotation.structure_scored"


class StoredEvent(BaseModel):
    sequence: int
    id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: str


class AgentRemark(BaseModel):
    """Payload for agent.remarked — a short in-personality feed line.

    Feed-flavor only: never gated (see AutonomyPolicy._NEVER_GATED), never
    projected (the Projector has no _apply branch for it, by design).
    """

    agent_name: str
    note: str


class ThreadPlanted(BaseModel):
    """Payload for thread.planted — mints a new thread's identity.

    `id` is the slug minted from `name` (see
    novelizer.canon.threads.slugify_thread_name) at plant time; every later
    thread.* event for this thread must cite this id, never re-derive it.
    """

    id: str
    name: str
    chapter_id: str = ""
    note: str = ""


class ThreadTouched(BaseModel):
    """Payload for thread.touched — an existing thread advances, cited by id."""

    id: str
    chapter_id: str = ""
    note: str = ""


class ThreadPaidOff(BaseModel):
    """Payload for thread.paid_off — an existing thread resolves, cited by id.

    Terminal: the ThreadsProjection treats this id as absorbing thereafter.
    """

    id: str
    chapter_id: str = ""
    note: str = ""


class ThreadAbandoned(BaseModel):
    """Payload for thread.abandoned — an existing thread is dropped, cited by id.

    Terminal: the ThreadsProjection treats this id as absorbing thereafter.
    """

    id: str
    chapter_id: str = ""
    note: str = ""


class AnnotationStructureScored(BaseModel):
    """Payload for annotation.structure_scored — one chapter's tension/pacing
    score, emitted by the Structure Analyst. Bounded: tension is a fraction
    in [0.0, 1.0], enforced at construction so an out-of-range LLM score
    fails fast rather than corrupting the projection.
    """

    chapter_id: str
    tension: float = Field(ge=0.0, le=1.0)
    pacing_label: str = ""
