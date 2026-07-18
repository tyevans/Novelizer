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
    SECRET_CREATED = "secret.created"
    SECRET_LEARNED = "secret.learned"
    SECRET_REFERENCED = "secret.referenced"
    SECRET_REVEALED = "secret.revealed"
    CAUSAL_EDGE_DECLARED = "causal_edge.declared"
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


class SecretCreated(BaseModel):
    """Payload for secret.created — mints a new secret's identity.

    `id` is the slug minted from `title` (see
    novelizer.canon.secrets.slugify_secret_name) at creation time; every
    later secret.* event for this secret must cite this id, never re-derive
    it (Locked decision #1). Any prose-producing agent (Author, Editor) may
    mint a secret; CharacterKeeper never does.
    """

    id: str
    title: str
    chapter_id: str = ""
    note: str = ""


class SecretLearned(BaseModel):
    """Payload for secret.learned — one character learns an existing secret,
    cited by id. Projected as a row in the secret_knowledge join table
    (idempotent: learning the same secret twice is a no-op, not a counter).
    """

    id: str
    character_id: str
    chapter_id: str = ""
    note: str = ""


class SecretReferenced(BaseModel):
    """Payload for secret.referenced — a character uses/references an
    existing secret in a chapter, cited by id. This is the durable,
    replayable 'uses' record M4.2's LeakDetector reads (Locked decision #3)
    — never deduped, every reference is its own fact.
    """

    id: str
    character_id: str
    chapter_id: str = ""
    note: str = ""


class SecretRevealed(BaseModel):
    """Payload for secret.revealed — an existing secret becomes public,
    cited by id. Secret-level, set-once: the KnowledgeProjection sets a
    `revealed` flag on the secret's own record once, never per character
    (Locked decision #2) — the matrix accessor derives `revealed` for every
    character, including ones created after this event.
    """

    id: str
    chapter_id: str = ""
    note: str = ""


class CausalEdgeDeclared(BaseModel):
    """Payload for causal_edge.declared — a claimed cause/effect relationship
    between two existing chapters. No minted identity and no lifecycle
    (Locked decision #4): every declaration is committed and projected as
    its own row, never deduped or superseded.
    """

    cause_chapter_id: str
    effect_chapter_id: str
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
