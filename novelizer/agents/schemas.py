from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator

# Must match novelizer.store.models.Domain — the draft stays dependency-free of
# the store layer, so the pairing is enforced by test_schemas instead of an import.
_DOMAINS = ("physical", "social", "metaphysical", "historical", "other")


class WorldEntryDraft(BaseModel):
    title: str
    body: str
    domain: Literal["physical", "social", "metaphysical", "historical", "other"] = "physical"
    tags: list[str] = Field(default_factory=list)
    supersedes_id: Optional[str] = None

    @field_validator("domain", mode="before")
    @classmethod
    def _coerce_unknown_domain(cls, v: object) -> object:
        # An out-of-enum answer (the live retconner got domain="character")
        # must never raise out of structured-output parsing or commit().
        return v if v in _DOMAINS else "other"


class WorldEntriesDraft(BaseModel):
    entries: list[WorldEntryDraft] = Field(default_factory=list)
    feed_note: str = ""
    no_action: bool = False


class NewCharacter(BaseModel):
    """One new character the Character Keeper observed in recent chapters but
    found missing from the cast. The system mints the id by slugging `name`
    (see novelizer.canon.characters.slugify_character_name); a slug colliding
    with an existing character is dropped at commit time, never re-created.
    """

    name: str
    traits: str = ""
    motivations: str = ""
    backstory: str = ""
    arc_status: str = ""
    voice: str = ""


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


class PromiseIntent(BaseModel):
    """One agent-declared ledger-promise action from structured output.

    `make` mints (name required); progress/pay/release cite an existing id
    exactly. `release` is the red-herring exit.
    """

    action: Literal["make", "progress", "pay", "release"]
    name: str = ""
    id: str = ""
    kind: Literal["foreshadow", "plant", "red_herring"] = "foreshadow"
    description: str = ""
    thread_id: str = ""
    note: str = ""


class ThemeIntent(BaseModel):
    """One agent-declared theme action from structured output.

    `introduce` mints a new theme from a freeform `title` (the system slugs it
    into an id — see novelizer.canon.themes.slugify_theme_name); `develop`
    must cite an existing theme's `id` rather than inventing one. Unlike
    threads/secrets, themes have no terminal states (no pay_off/abandon/reveal).
    `BaseAgent._commit_theme_intents` turns validated intents into theme.*
    commits (see novelizer/agents/base.py). This plan/schema implements
    M5.2 Locked decision 6 (theme action vocabulary).
    """

    action: Literal["introduce", "develop"]
    title: str = ""
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
    new_characters: list[NewCharacter] = Field(default_factory=list)
    updated_characters: list[CharacterUpdate] = Field(default_factory=list)
    retcon_requests: list[RetconDraft] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    feed_note: str = ""
    no_action: bool = False


class VoiceDriftFlag(BaseModel):
    """One agent-declared instance of a character's prose voice violating its
    voice card, from Editor structured output. Cited at commit time as a
    tagged retcon_request.created (see novelizer/agents/editor.py's
    VOICE_SOURCE_TAG), never a direct canon mutation.
    """

    character_id: str
    line: str
    trait_violated: str
    note: str = ""


class EditorVerdict(BaseModel):
    verdict: Literal["approve", "revise"] = "approve"
    notes: str = ""
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)
    theme_intents: list[ThemeIntent] = Field(default_factory=list)
    voice_drift_flags: list[VoiceDriftFlag] = Field(default_factory=list)


class ContinuityOutput(BaseModel):
    retcon_requests: list[RetconDraft] = Field(default_factory=list)
    feed_note: str = ""
    no_action: bool = False


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


class MinedSecretFact(BaseModel):
    """A secret-knowledge fact extracted by prose mining from a chapter's prose.

    Mining cites existing secret ids only (mining never invents new secret
    identity via 'plant' -- see M5.1 Locked decision 3). `action` is restricted
    to 'learn' and 'uses' (reveal actions are modeled separately as
    MinedRevealFact). `known_id` signals whether the miner recognized the secret
    id in the active knowledge matrix -- if False, the fact escalates to
    retcon_request.created rather than auto-committing.
    """

    action: Literal["learn", "uses"]
    id: str
    character_id: str
    chapter_id: str
    known_id: bool = True
    note: str = ""


class MinedRevealFact(BaseModel):
    """A secret-reveal fact extracted by prose mining.

    Unlike MinedSecretFact, reveal facts always escalate to retcon_request.created
    unconditionally, regardless of `known_id`, per M5.1 Locked decision 3 --
    mining never auto-commits a secret.revealed event. The `known_id` field is
    retained for consistency and to describe to the retcon whether the secret id
    was even recognized.
    """

    id: str
    chapter_id: str
    known_id: bool = True
    note: str = ""


class MinedThreadFact(BaseModel):
    """A plot-thread fact extracted by prose mining.

    Mining cites existing thread ids only (mining never mints new thread identity
    via 'plant' -- see M5.1 Locked decision 3). `action` is restricted to
    'touch', 'planted', and 'paid_off' (not 'plant' or 'abandon' -- the latter
    are agent authoring actions, not prose-mined observations). `known_id` signals
    whether the miner recognized the thread id -- if False, the fact escalates to
    retcon_request.created rather than auto-committing.
    """

    action: Literal["touch", "planted", "paid_off"]
    id: str
    chapter_id: str
    known_id: bool = True
    note: str = ""


class MinedCausalFact(BaseModel):
    """A causal-edge fact extracted by prose mining.

    Unlike mined secret/thread facts, causal facts cite chapter ids, which are
    always known to the miner (they come from ctx["chapters"]/chapter_order), so
    there is no ambiguity axis and no `known_id` field. Dedup for causal facts
    is exact triple-match against existing edges, not an escalate-on-ambiguity path.
    """

    cause_chapter_id: str
    effect_chapter_id: str
    note: str = ""


class MinedInspirationFact(BaseModel):
    """A dealt Muse inspiration item the prose visibly uses, extracted by the
    mining pass. `item` must repeat a dealt entry from the chapter's hand (the
    prompt lists them); a non-matching item is dropped at commit time with a
    logged info line, never a retcon — uptake is a health metric, not canon.
    Name uptake is NOT mined: it's recorded mechanically at CharacterKeeper
    mint time, so `kind` has no "names" arm.
    """

    kind: Literal["professions", "settings", "beats"]
    item: str


class MinedFactsOutput(BaseModel):
    """Structured output from the prose-mining pass of the Continuity Checker.

    The mining pass reads a chapter's prose and returns a set of facts
    (secret/reveal/thread/causal) that the prose shows but the log may not
    have covering events for. See M5.1 for the mining architecture and
    commit/dedup logic.
    """

    secret_facts: list[MinedSecretFact] = Field(default_factory=list)
    reveal_facts: list[MinedRevealFact] = Field(default_factory=list)
    thread_facts: list[MinedThreadFact] = Field(default_factory=list)
    causal_facts: list[MinedCausalFact] = Field(default_factory=list)
    inspiration_facts: list[MinedInspirationFact] = Field(default_factory=list)
    feed_note: str = ""
