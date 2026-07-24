from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, BeforeValidator, Field


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
    retired = "retired"


class EditorialStatus(StrEnum):
    draft = "draft"
    reviewed = "reviewed"
    final = "final"


class SignalKind(StrEnum):
    seed = "seed"
    focus = "focus"
    override = "override"
    note = "note"
    revise = "revise"


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
    window_lo: int = 0
    window_hi: int = 0
    planned_payoff_note: str = ""


class PromiseState(StrEnum):
    open = "open"
    paid = "paid"
    released = "released"


class PromiseRecord(BaseModel):
    """Read-side row for a ledger promise, built and rebuilt by the Projector
    from the promise.* event log. Promise events after the first carry only
    deltas (id + note), so fields accumulate across events. `paid` and
    `released` are absorbing (see canon.promises.TERMINAL_PROMISE_STATES);
    released is the alarm-free exit for red herrings."""

    id: str
    name: str
    description: str = ""
    kind: str = "foreshadow"
    state: PromiseState = PromiseState.open
    thread_id: str = ""
    setup_chapter_id: str = ""
    window_lo: int = 0
    window_hi: int = 0
    progress_count: int = 0
    last_note: str = ""
    last_chapter_id: str = ""


class BriefStatus(StrEnum):
    open = "open"
    superseded = "superseded"
    fulfilled = "fulfilled"


class BeatRecord(BaseModel):
    """Read-side row for a plot beat in a blueprint, built and rebuilt by the
    Projector from the beat.* event log. Beats represent structural anchors
    within a story framework (e.g., turning points, set pieces), indexed to
    an ideal position and matched against chapter structure.
    """

    id: str
    blueprint_id: str
    slug: str
    name: str
    ideal_pct: float
    tolerance_pct: float
    expected_polarity: str = ""
    fulfilled_by_chapter_id: str = ""
    note: str = ""


class BlueprintRecord(BaseModel):
    """Read-side row for a story blueprint, built and rebuilt by the Projector
    from the blueprint.* event log. Blueprints define structural frameworks
    (e.g., three-act, hero's journey) and provide scaffolding for chapter
    planning. `active` is set-once per adoption; a new blueprint adoption
    supersedes the prior active blueprint.
    """

    id: str
    framework: str
    target_chapter_count: int
    genre: str = ""
    obligatory_scenes: list[str] = Field(default_factory=list)
    active: bool = True
    note: str = ""
    completed: bool = False
    completed_chapter_id: str = ""
    completed_note: str = ""


class ChapterBriefRecord(BaseModel):
    """Read-side row for one planned chapter brief, built and rebuilt by the
    Projector from the chapter_brief.* event log. Briefs capture high-level
    structural and narrative intentions for a chapter before prose is drafted,
    including goals, viewpoint, threads and promises to weave, and plot beats
    to hit. `superseded` and `fulfilled` are absorbing states.
    """

    id: str
    target_ordinal: int
    goal: str
    pov_character_id: str = ""
    threads_to_touch: list[str] = Field(default_factory=list)
    beats_to_hit: list[str] = Field(default_factory=list)
    promises_to_progress: list[str] = Field(default_factory=list)
    value_shift: str = ""
    planned_outcome: str = ""
    synopsis: str = ""
    status: BriefStatus = BriefStatus.open
    superseded_by_brief_id: str = ""
    fulfilled_by_chapter_id: str = ""


class ArcPivot(BaseModel):
    beat_id: str
    description: str = ""


class ArcRecord(BaseModel):
    """Read-side row for a character's planned arc, built and rebuilt by the
    Projector from the arc.* event log. Delta-accumulating like ThreadRecord;
    arcs capture the character's internal growth, conflicts, and resolution
    across the story. `active` flips on per-character supersession; `resolved`
    is absorbing. Pivots track key beat moments aligned to the arc.
    """

    id: str
    character_id: str
    arc_type: str
    ghost: str = ""
    lie: str = ""
    truth: str = ""
    want: str = ""
    need: str = ""
    active: bool = True
    resolved: bool = False
    outcome: str = ""
    resolved_chapter_id: str = ""
    advance_count: int = 0
    last_note: str = ""
    last_chapter_id: str = ""
    pivots: list[ArcPivot] = Field(default_factory=list)


class ThemeRecord(BaseModel):
    """Read-side row for a theme/motif, built and rebuilt by the Projector
    from the theme.* event log (see novelizer/canon/projector.py). Unlike
    ThreadRecord, themes have no terminal state (M5.2 Locked decision 6) --
    a theme can be developed indefinitely, so there is no `state` field.
    """

    id: str
    title: str
    touch_count: int = 0
    last_note: str = ""
    last_chapter_id: str = ""


class SecretRecord(BaseModel):
    """Read-side row for a secret, built and rebuilt by the Projector from
    the secret.* event log (see novelizer/canon/projector.py). `revealed`
    is secret-level, set-once state (Locked decision #2 in M4's spec) — it
    is never written per character; ReadStore.knowledge_matrix() and
    novelizer.canon.secrets.knowledge_cell_state derive the per-character
    cell from this flag plus the secret_knowledge join table.
    """

    id: str
    title: str
    revealed: bool = False
    reveal_window_lo: int = 0
    reveal_window_hi: int = 0


class CausalEdgeRecord(BaseModel):
    """Read-side row for one declared causal edge, built by the Projector
    from causal_edge.declared events. No minted identity and no
    deduplication (Locked decision #4) — every declared edge, including an
    exact repeat, is its own row.
    """

    cause_chapter_id: str
    effect_chapter_id: str
    note: str = ""


class SecretReferenceRecord(BaseModel):
    """Read-side row for one secret.referenced event — the durable,
    replayable 'uses' record M4.2's LeakDetector reads (Locked decision #3).
    Never deduped: every reference is committed and projected as its own row.
    """

    secret_id: str
    character_id: str
    chapter_id: str = ""
    note: str = ""


class StructureScore(BaseModel):
    """Read-side row for one chapter's narrative-structure score, built by
    the Projector from annotation.structure_scored events (see
    novelizer/canon/projector.py) and consumed by novelizer/brain/sag_spike.py's
    pure detect_sag_spike function and, in M3.3, the Story Shape TUI view.
    """

    chapter_id: str
    tension: float = Field(ge=0.0, le=1.0)
    pacing_label: str = ""


class ChapterSummary(BaseModel):
    """Read-side row for one chapter's rolling summary, built by the Projector
    from chapter.summarized events. gist feeds the pull-mode chapter map;
    summary feeds advisory (push-mode) contexts. Upsert by chapter_id: a
    re-summarize after chapter.revised replaces the row."""

    chapter_id: str
    gist: str = ""
    summary: str = ""


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
    provenance: Optional[dict] = None
    revision_count: int = 0
    """How many chapter.revised events this chapter has absorbed, counted by
    the projector. A revision returns the chapter to `draft`, which puts it
    straight back in the Editor's queue, so without a count the
    Editor -> Author loop has no natural end. Defaults to 0 so chapters
    projected before this field replay unchanged."""


class FlagStatus(StrEnum):
    open = "open"
    resolved = "resolved"
    rejected = "rejected"
    stale = "stale"


class Flag(BaseModel):
    """A structured issue any agent can raise mid-work — a generalization of
    the old contradiction-only RetconRequest. `category` is free-form
    (e.g. "contradiction", "pacing", "thematic", "worldbuilding", "voice_drift")
    so agents aren't limited to a fixed taxonomy; the Triage agent routes by
    category via a small owner map, catch-alling anything unmapped.
    `triage_passes` counts unresolved catch-all Triage passes over an unowned
    flag; past a threshold it is marked `stale` rather than looping forever.
    `severity` is assessed by Triage alongside its real/dismiss verdict.
    `escalated` mirrors whether an unresolved FLAG_ESCALATED currently
    applies (cleared by FLAG_ESCALATION_CLEARED or flag resolution).
    `failed_attempts` counts owning-agent decline/fail cycles; past a
    threshold it triggers escalation regardless of original severity.
    """
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    category: str
    description: str
    related_entry_ids: list[str] = Field(default_factory=list)
    proposed_resolution: str = ""
    status: FlagStatus = FlagStatus.open
    filed_by: str = ""
    resolved_by: Optional[str] = None
    triage_passes: int = 0
    severity: Optional[Literal["minor", "major", "critical"]] = None
    escalated: bool = False
    failed_attempts: int = 0
    escalation_cleared_by: Optional[Literal["agent", "human"]] = None
    escalation_clear_note: Optional[str] = None


def _tolerant_signal_kind(v: object) -> object:
    """Normalize known kinds to SignalKind; pass unknown ones through raw.

    Event sourcing: persisted director_signal events must stay readable
    forever. A closed enum here poisons the read side the moment any writer
    mints a kind this model version does not know — Scheduler.tick validates
    every unconsumed signal each cycle, so one such event wedged the live
    scheduler on every cycle (kind='revise' read by a pre-revise SignalKind).
    """
    try:
        return SignalKind(v)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return v  # unknown kind from a newer/older writer: keep the raw value


class DirectorSignal(BaseModel):
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    # SignalKind for every kind this version knows; raw str for kinds minted
    # by other model versions (still a required, non-empty-typed field).
    kind: Annotated[SignalKind | str, BeforeValidator(_tolerant_signal_kind)]
    body: str
    target_agent: Optional[str] = None
    target_entity: str = ""
    consumed: bool = False


class ChatMessageRecord(BaseModel):
    """One projected chat message. role is 'user' (the Director) or 'agent'."""

    agent_name: str
    role: str
    text: str
    message_id: str


class HandStatus(StrEnum):
    active = "active"
    consumed = "consumed"
    superseded = "superseded"


class InspirationHandRecord(BaseModel):
    """Read-side row for one dealt Muse hand, built by the Projector from
    inspiration.* events. `consumed` and `superseded` are both absorbing —
    whichever is applied first while the hand is active wins.
    """

    id: str
    seed: int
    corpus_version: str
    era: str
    names: list[str] = Field(default_factory=list)
    professions: list[str] = Field(default_factory=list)
    settings: list[str] = Field(default_factory=list)
    beats: list[str] = Field(default_factory=list)
    status: HandStatus = HandStatus.active
    consumed_chapter_id: str = ""


class InspirationUptakeRecord(BaseModel):
    """Read-side row for one inspiration.uptake_recorded event, deduped by
    (hand_id, kind, item) at the projection so repeated mining runs never
    inflate the uptake rate.
    """

    hand_id: str
    kind: str
    item: str
    chapter_id: str = ""
