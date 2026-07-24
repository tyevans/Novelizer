from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class EventType:
    WORLD_ENTRY_CREATED = "world_entry.created"
    WORLD_ENTRY_SUPERSEDED = "world_entry.superseded"
    WORLD_ENTRY_RETIRED = "world_entry.retired"
    CHARACTER_CREATED = "character.created"
    CHARACTER_UPDATED = "character.updated"
    CHAPTER_CREATED = "chapter.created"
    CHAPTER_STATUS_CHANGED = "chapter.status_changed"
    CHAPTER_REVISED = "chapter.revised"
    DIRECTOR_SIGNAL_CREATED = "director_signal.created"
    DIRECTOR_SIGNAL_CONSUMED = "director_signal.consumed"
    RETCON_REQUEST_CREATED = "retcon_request.created"  # legacy alias only, see projector.py
    RETCON_REQUEST_RESOLVED = "retcon_request.resolved"  # legacy alias only, see projector.py
    RETCON_REQUEST_REJECTED = "retcon_request.rejected"  # legacy alias only, see projector.py
    FLAG_CREATED = "flag.created"
    FLAG_RESOLVED = "flag.resolved"
    FLAG_REJECTED = "flag.rejected"
    FLAG_ESCALATED = "flag.escalated"
    FLAG_ESCALATION_CLEARED = "flag.escalation_cleared"
    PROPOSAL_CREATED = "proposal.created"
    PROPOSAL_APPROVED = "proposal.approved"
    PROPOSAL_REJECTED = "proposal.rejected"
    AUTONOMY_CHANGED = "autonomy.changed"
    AGENT_REMARKED = "agent.remarked"
    CHAT_USER_MESSAGED = "chat.user_messaged"
    CHAT_AGENT_REPLIED = "chat.agent_replied"
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
    CHAPTER_MINED = "chapter.mined"
    CHAPTER_PROCESSED = "chapter.processed"
    CHAPTER_SUMMARIZED = "chapter.summarized"
    THEME_INTRODUCED = "theme.introduced"
    THEME_DEVELOPED = "theme.developed"
    INSPIRATION_DRAWN = "inspiration.drawn"
    INSPIRATION_HAND_CONSUMED = "inspiration.hand_consumed"
    INSPIRATION_HAND_SUPERSEDED = "inspiration.hand_superseded"
    INSPIRATION_UPTAKE_RECORDED = "inspiration.uptake_recorded"
    PROMISE_MADE = "promise.made"
    PROMISE_PROGRESSED = "promise.progressed"
    PROMISE_PAID = "promise.paid"
    PROMISE_RELEASED = "promise.released"
    THREAD_RESOLUTION_PLANNED = "thread.resolution_planned"
    SECRET_REVEAL_PLANNED = "secret.reveal_planned"
    BLUEPRINT_ADOPTED = "blueprint.adopted"
    BLUEPRINT_RETARGETED = "blueprint.retargeted"
    BEAT_FULFILLED = "beat.fulfilled"
    CHAPTER_BRIEF_DRAFTED = "chapter_brief.drafted"
    CHAPTER_BRIEF_SUPERSEDED = "chapter_brief.superseded"
    CHAPTER_BRIEF_FULFILLED = "chapter_brief.fulfilled"
    ARC_DECLARED = "arc.declared"
    ARC_PIVOT_PLANNED = "arc.pivot_planned"
    ARC_ADVANCED = "arc.advanced"
    ARC_RESOLVED = "arc.resolved"
    BOOK_COMPLETED = "book.completed"


class StoredEvent(BaseModel):
    sequence: int
    id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: str
    run_id: str | None = None


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
    source: str = "declared"
    """source distinguishes agent-declared facts ('declared', default) from
    Continuity Checker prose-mined facts ('mined') -- see M5.1."""


class ThreadTouched(BaseModel):
    """Payload for thread.touched — an existing thread advances, cited by id."""

    id: str
    chapter_id: str = ""
    note: str = ""
    source: str = "declared"
    """source distinguishes agent-declared facts ('declared', default) from
    Continuity Checker prose-mined facts ('mined') -- see M5.1."""

    evidence: str = ""
    """Canon the declaring agent cited as grounding. Defaults empty so events
    written before this field replay unchanged."""

class ThreadPaidOff(BaseModel):
    """Payload for thread.paid_off — an existing thread resolves, cited by id.

    Terminal: the ThreadsProjection treats this id as absorbing thereafter.
    """

    id: str
    chapter_id: str = ""
    note: str = ""
    source: str = "declared"
    """source distinguishes agent-declared facts ('declared', default) from
    Continuity Checker prose-mined facts ('mined') -- see M5.1."""

    evidence: str = ""
    """Canon the declaring agent cited as grounding. Defaults empty so events
    written before this field replay unchanged."""

class ThreadAbandoned(BaseModel):
    """Payload for thread.abandoned — an existing thread is dropped, cited by id.

    Terminal: the ThreadsProjection treats this id as absorbing thereafter.
    """

    id: str
    chapter_id: str = ""
    note: str = ""

    evidence: str = ""
    """Canon the declaring agent cited as grounding. Defaults empty so events
    written before this field replay unchanged."""

class ThemeIntroduced(BaseModel):
    """Payload for theme.introduced — mints a new theme's identity.

    `id` is the slug minted from `title` (see
    novelizer.canon.themes.slugify_theme_name) at introduce time; every
    later theme.* event for this theme must cite this id, never re-derive
    it. Themes have no terminal state (M5.2 Locked decision 6) — unlike
    ThreadPlanted's descendants, there is no paid_off/abandoned equivalent.
    """

    id: str
    title: str
    chapter_id: str = ""
    note: str = ""
    source: str = "declared"
    """source distinguishes agent-declared facts ('declared', default) from
    a hypothetical future mining extension ('mined') -- plumbing only, see
    M5.2 Decision Note D1; nothing in M5.2 ever sets this to 'mined'."""


class ThemeDeveloped(BaseModel):
    """Payload for theme.developed — an existing theme advances, cited by id.

    No terminal state (M5.2 Locked decision 6): a theme can be developed
    indefinitely across the manuscript, unlike thread.touched's eventual
    paid_off/abandoned absorption.
    """

    id: str
    chapter_id: str = ""
    note: str = ""
    source: str = "declared"
    """source distinguishes agent-declared facts ('declared', default) from
    a hypothetical future mining extension ('mined') -- plumbing only, see
    M5.2 Decision Note D1; nothing in M5.2 ever sets this to 'mined'."""


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
    source: str = "declared"
    """source distinguishes agent-declared facts ('declared', default) from
    Continuity Checker prose-mined facts ('mined') -- see M5.1."""

    evidence: str = ""
    """Canon the declaring agent cited as grounding. Defaults empty so events
    written before this field replay unchanged."""

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
    source: str = "declared"
    """source distinguishes agent-declared facts ('declared', default) from
    Continuity Checker prose-mined facts ('mined') -- see M5.1."""

    evidence: str = ""
    """Canon the declaring agent cited as grounding. Defaults empty so events
    written before this field replay unchanged."""

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

    evidence: str = ""
    """Canon the declaring agent cited as grounding. Defaults empty so events
    written before this field replay unchanged."""

class CausalEdgeDeclared(BaseModel):
    """Payload for causal_edge.declared — a claimed cause/effect relationship
    between two existing chapters. No minted identity and no lifecycle
    (Locked decision #4): every declaration is committed and projected as
    its own row, never deduped or superseded.
    """

    cause_chapter_id: str
    effect_chapter_id: str
    note: str = ""
    source: str = "declared"
    """source distinguishes agent-declared facts ('declared', default) from
    Continuity Checker prose-mined facts ('mined') -- see M5.1."""

    evidence: str = ""
    """Canon the declaring agent cited as grounding. Defaults empty so events
    written before this field replay unchanged."""

class AnnotationStructureScored(BaseModel):
    """Payload for annotation.structure_scored — one chapter's tension/pacing
    score, emitted by the Structure Analyst. Bounded: tension is a fraction
    in [0.0, 1.0], enforced at construction so an out-of-range LLM score
    fails fast rather than corrupting the projection.
    """

    chapter_id: str
    tension: float = Field(ge=0.0, le=1.0)
    pacing_label: str = ""


class WorldEntryRetired(BaseModel):
    """Payload for world_entry.retired — a tombstone. The entry named by
    entry_id leaves active canon with no successor (distinct from
    world_entry.superseded, which always names a replacement). The full body
    stays in the event log for provenance; the read model flips the row to
    canon_status='retired' and the indexer drops it from search. `flag_id`
    cites the curation flag that authorized the retirement.
    """

    entry_id: str
    reason: str = ""
    flag_id: str = ""


class ChapterRevised(BaseModel):
    """Payload for chapter.revised — an existing chapter's prose is rewritten
    in place, cited by id (Locked decision 10). No minted identity and no
    new title/character_ids: a revision changes prose, not the chapter's
    identity or cast. The Projector folds this into the existing chapter
    row and resets editorial_status back to draft, re-entering the
    draft->review cycle instead of leaving it in whatever status it was
    flagged at.
    """

    chapter_id: str
    prose: str
    editor_notes_ref: str = ""


class ChapterMined(BaseModel):
    """Payload for chapter.mined -- bookkeeping marker that the prose-mining
    pass has run for this chapter. Never projected (no _apply branch in
    Projector), same class as AgentRemark. Gives mining idempotency without
    a new persisted 'already mined' flag or a re-scan of the full log's
    prose every cycle (M5.1 Locked decision 2).
    """

    chapter_id: str


class ChapterProcessed(BaseModel):
    """Payload for chapter.processed -- per-agent bookkeeping marker that
    `agent` has seen this chapter's full prose. Never projected; done-sets are
    a pure log fold (brain/watermarks.current_done_ids), and a later
    chapter.revised clears the marker so revised chapters re-process."""

    agent: str
    chapter_id: str


class ChapterSummarized(BaseModel):
    """Payload for chapter.summarized -- the Summarizer's rolling summary of
    one chapter revision. Projected into chapter_summaries (upsert by
    chapter_id: the latest summary wins on replay). gist is one line for the
    pull-mode chapter map; summary is one paragraph for advisory contexts."""

    chapter_id: str
    gist: str
    summary: str


class ChatUserMessaged(BaseModel):
    """Payload for chat.user_messaged — the Director speaks to one agent.

    One conversation per agent: the event's aggregate_id is the agent name.
    Never gated (chat is a first-person channel, not an agent canon write).
    """

    message_id: str
    agent_name: str
    text: str


class ChatAgentReplied(BaseModel):
    """Payload for chat.agent_replied — an agent's completed chat reply.

    Committed only when generation completes; a failed generation commits
    nothing (the log never records a failure as speech). `replying_to` cites
    the chat.user_messaged message_id that prompted this reply.
    """

    message_id: str
    agent_name: str
    text: str
    replying_to: str = ""


def _default_authority() -> dict[str, str]:
    return {"names": "binding", "professions": "inspiration",
            "settings": "inspiration", "beats": "inspiration"}


class InspirationDrawn(BaseModel):
    """Payload for inspiration.drawn — the Muse deals a hand of PRNG draws
    from bundled corpora. `seed` is sourced from OS entropy at deal time and
    recorded here, so replaying the log reproduces the exact draw via
    novelizer.muse.draws.deal_hand (fresh entropy forward, deterministic
    replay back). `authority` carries the per-kind force level so raising
    beat authority later is a settings change, not a schema change.
    """

    hand_id: str
    seed: int
    corpus_version: str
    era: str
    names: list[str] = Field(default_factory=list)
    professions: list[str] = Field(default_factory=list)
    settings: list[str] = Field(default_factory=list)
    beats: list[str] = Field(default_factory=list)
    target_agent: str = "author"
    authority: dict[str, str] = Field(default_factory=_default_authority)


class InspirationHandConsumed(BaseModel):
    """Payload for inspiration.hand_consumed — the Author committed a chapter
    while this hand was live. Consumed is absorbing (like terminal threads):
    a later supersede for a consumed hand is a projection no-op.
    """

    hand_id: str
    chapter_id: str = ""


class InspirationHandSuperseded(BaseModel):
    """Payload for inspiration.hand_superseded — a director reroll discarded
    the hand before any chapter used it. The draw stays in the log as a fact.
    """

    hand_id: str


class InspirationUptakeRecorded(BaseModel):
    """Payload for inspiration.uptake_recorded — one dealt item visibly landed
    in prose. `item` is the dealt item verbatim (never the prose's variant),
    so the projection's (hand_id, kind, item) key dedupes re-mining runs.
    """

    hand_id: str
    kind: str
    item: str
    chapter_id: str = ""


class PromiseMade(BaseModel):
    """Payload for promise.made — mints a new promise's identity.

    A promise is a discrete planted expectation (Chekhov's gun, foreshadowed
    image, red herring) with a discrete payoff — below thread scale. `id` is
    the slug minted from `name` (see novelizer.canon.promises
    .slugify_promise_name) at make time; every later promise.* event must
    cite this id, never re-derive it (Locked decision #1: first-make-wins,
    same as threads).

    `window_lo`/`window_hi` are 1-based chapter ordinals bounding the target
    payoff window; 0 means unset. `kind` is one of foreshadow|plant|
    red_herring — red herrings exit via promise.released without alarm.
    """

    id: str
    name: str
    description: str = ""
    kind: str = "foreshadow"
    chapter_id: str = ""
    thread_id: str = ""
    window_lo: int = 0
    window_hi: int = 0
    note: str = ""
    source: str = "declared"


class PromiseProgressed(BaseModel):
    """Payload for promise.progressed — an existing promise advances, cited
    by id. Progress on a terminal promise is a no-op in projection."""

    id: str
    chapter_id: str = ""
    note: str = ""
    source: str = "declared"


class PromisePaid(BaseModel):
    """Payload for promise.paid — the planted expectation is fulfilled,
    cited by id. Terminal: the PromisesProjection treats this id as
    absorbing thereafter (Locked decision #2)."""

    id: str
    chapter_id: str = ""
    note: str = ""
    source: str = "declared"


class PromiseReleased(BaseModel):
    """Payload for promise.released — the sanctioned exit for red herrings
    and deliberate abandonment, cited by id. Terminal and absorbing, like
    promise.paid; released promises never alarm."""

    id: str
    reason: str = ""
    chapter_id: str = ""
    source: str = "declared"


class ThreadResolutionPlanned(BaseModel):
    """Payload for thread.resolution_planned — pins a target resolution
    window on an existing, non-terminal thread, cited by id.

    Re-emission supersedes (Locked decision: the event history IS the record
    of schedule slips). Unknown or terminal thread ids are projection no-ops.
    `window_lo`/`window_hi` are 1-based chapter ordinals; 0 clears the plan.
    """

    id: str
    window_lo: int = 0
    window_hi: int = 0
    planned_payoff_note: str = ""


class SecretRevealPlanned(BaseModel):
    """Payload for secret.reveal_planned — pins a target reveal window on an
    existing, unrevealed secret, cited by id. Re-emission supersedes; unknown
    or already-revealed secret ids are projection no-ops. Windows are 1-based
    chapter ordinals; 0 clears the plan."""

    id: str
    window_lo: int = 0
    window_hi: int = 0


class BeatSpec(BaseModel):
    """One beat minted with a blueprint. beat_id = f"{blueprint_id}-{slug}"
    — minted once at adoption; beat.fulfilled cites it exactly."""

    beat_id: str
    slug: str
    name: str
    ideal_pct: float
    tolerance_pct: float
    expected_polarity: str = ""


class BlueprintAdopted(BaseModel):
    """Payload for blueprint.adopted — mints the story's structural frame.

    ALWAYS routed through the proposal queue regardless of autonomy level
    (Locked decision #1: adopting a shape re-frames the whole book; the
    Director signs off). One blueprint is active at a time: adoption
    supersedes any prior blueprint in projection (Locked decision #2).
    `blueprint_id` is minted by the proposing side (uuid); beats are minted
    with it from a template (canon/beat_templates.py).

    Sanctioned exception: Director-authored adoption at story creation, via
    the story picker's Frame step (director/commands.adopt_blueprint_story_dir).
    This runs before any Runtime/GatingCommitter exists -- the creation form
    IS the sign-off, so it appends directly rather than through a proposal.
    Agent-proposed adoption remains always-gated; this exception never
    applies to commit_blueprint_plan's caller."""

    blueprint_id: str
    framework: str
    target_chapter_count: int
    genre: str = ""
    beats: list[BeatSpec] = Field(default_factory=list)
    obligatory_scenes: list[str] = Field(default_factory=list)
    note: str = ""


class BlueprintRetargeted(BaseModel):
    """Payload for blueprint.retargeted — the book is running long/short;
    beat windows recompute from the new count in read-side logic. Cites the
    active blueprint id; unknown/superseded ids are projection no-ops."""

    blueprint_id: str
    target_chapter_count: int


class BeatFulfilled(BaseModel):
    """Payload for beat.fulfilled — the Plotter judges a drafted chapter
    carried the beat, cited by beat_id. Re-emission supersedes (the room may
    re-judge which chapter truly carried the midpoint). chapter_id="" clears
    a fulfillment."""

    beat_id: str
    chapter_id: str = ""
    note: str = ""


class ChapterBriefDrafted(BaseModel):
    """Payload for chapter_brief.drafted — the plan for a near-future
    chapter; the Plotter's main output, the Author's assignment.

    `brief_id` minted once (uuid) at draft. `target_ordinal` is a 1-based
    future chapter ordinal; briefs for already-drafted ordinals are dropped
    at commit (plan the future, not the past). Cited thread/beat/promise ids
    are validated at commit; unknown ids are dropped from the lists with a
    warning, never fail the brief."""

    brief_id: str
    target_ordinal: int
    goal: str
    pov_character_id: str = ""
    threads_to_touch: list[str] = Field(default_factory=list)
    beats_to_hit: list[str] = Field(default_factory=list)
    promises_to_progress: list[str] = Field(default_factory=list)
    value_shift: str = ""        # e.g. "trust: + -> -"
    planned_outcome: str = ""    # yes | yes_but | no_and | no
    synopsis: str = ""


class ChapterBriefSuperseded(BaseModel):
    """Payload for chapter_brief.superseded — terminal; the replacing brief
    (if any) is its own drafted event."""

    brief_id: str
    superseded_by_brief_id: str = ""


class ChapterBriefFulfilled(BaseModel):
    """Payload for chapter_brief.fulfilled — the Author drafted against this
    brief; terminal and absorbing."""

    brief_id: str
    chapter_id: str


class ArcDeclared(BaseModel):
    """Payload for arc.declared — mints a character's planned arc.

    `arc_id` is minted (uuid) by the committing helper. One arc is ACTIVE per
    character at a time: declaring supersedes the character's prior active arc
    in projection (Locked decision #1, mirroring blueprint supersession scoped
    to character_id). The declared plan complements Character.arc_status (the
    Keeper's observed snapshot) — it never replaces it."""
    arc_id: str
    character_id: str
    arc_type: str            # positive|flat|disillusionment|fall|corruption
    ghost: str = ""
    lie: str = ""
    truth: str = ""
    want: str = ""
    need: str = ""
    note: str = ""


class ArcPivotPlanned(BaseModel):
    """Payload for arc.pivot_planned — pins an internal pivot to a blueprint
    beat, cited by arc_id + beat_id. Re-emission for the same (arc, beat)
    supersedes. Unknown/resolved arcs and unknown beats are projection no-ops."""
    arc_id: str
    beat_id: str
    description: str = ""


class ArcAdvanced(BaseModel):
    """Payload for arc.advanced — evidence the arc moved in a chapter, cited
    by arc_id. No-op on resolved arcs."""
    arc_id: str
    chapter_id: str = ""
    note: str = ""


class ArcResolved(BaseModel):
    """Payload for arc.resolved — terminal and absorbing. `outcome` is one of
    truth_embraced|lie_embraced|truth_tragic|world_changed; an outcome
    inconsistent with the declared arc_type is an arc_alignment ALARM for the
    Director, never a projection error (Locked decision #2)."""
    arc_id: str
    chapter_id: str = ""
    outcome: str = ""
    note: str = ""


class BookCompleted(BaseModel):
    """Payload for book.completed — the room declares the blueprint satisfied:
    every beat fulfilled, every promise paid or released, every active arc
    resolved (see novelizer.brain.completion).

    Informational and one-shot per blueprint: the projection ignores a repeat
    while the same blueprint is active, and adopting a new blueprint clears
    the flag (Locked decision: completion describes the CURRENT shape). It
    does not stop the scheduler — the room quiesces on readiness, and the
    Director decides when to close the story."""
    blueprint_id: str
    chapter_id: str = ""     # the last chapter at declaration time
    note: str = ""
