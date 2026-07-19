from __future__ import annotations
import logging
from typing import Protocol
from pydantic import BaseModel, Field
from novelizer.canon.events import (
    EventType, AgentRemark, ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned,
    SecretCreated, SecretLearned, SecretReferenced, SecretRevealed, CausalEdgeDeclared,
    ThemeIntroduced, ThemeDeveloped,
)
from novelizer.canon.threads import slugify_thread_name
from novelizer.canon.secrets import slugify_secret_name
from novelizer.canon.themes import slugify_theme_name
from novelizer.agents.schemas import ThreadIntent, KnowledgeIntent, CausalIntent, ThemeIntent
from novelizer.store.models import RetconRequest, RetconStatus

logger = logging.getLogger(__name__)


def _normalize_id(raw: str) -> str:
    """Canonicalize an agent- or LLM-supplied id for comparison/storage.

    Canon ids are minted lowercase everywhere (every `slugify_*_name`
    output), so a casing mismatch on a *citing* id (not a minting one) is a
    correctness bug, not an unknown-id case. Applied only at
    membership-check/payload-construction sites in the commit helpers below
    -- never to minting logic.
    """
    return raw.strip().lower()


_KNOWLEDGE_EVENT_BY_ACTION = {
    "learn": (EventType.SECRET_LEARNED, SecretLearned),
    "reveal": (EventType.SECRET_REVEALED, SecretRevealed),
    "uses": (EventType.SECRET_REFERENCED, SecretReferenced),
}


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)
    theme_intents: list[ThemeIntent] = Field(default_factory=list)


class Runner(Protocol):
    async def ainvoke(self, inputs: dict) -> dict: ...


class BaseAgent:
    name: str = "agent"

    def __init__(
        self,
        runner,
        read_store,
        committer,
        interval: int,
        name: str | None = None,
        personality: str = "",
    ) -> None:
        self._runner = runner
        self._read = read_store
        self._committer = committer
        self.interval = interval
        if name is not None:
            self.name = name
        self.personality = personality
        self.paused = False
        self._last_run = 0.0

    @staticmethod
    def _guarded_line(label: str, value: str) -> str:
        """Return an optional "\n\n{label}: {value}" line, or "" if value is falsy."""
        return f"\n\n{label}: {value}" if value else ""

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def ready_for_interval(self, now: float) -> bool:
        return (now - self._last_run) >= self.interval

    def mark_ran(self, now: float) -> None:
        self._last_run = now

    async def readiness(self) -> float:
        return 0.0

    async def run_once(self) -> None:
        pass

    async def _consume_signals(self, signals) -> None:
        for sig in signals:
            consumed = sig.model_copy(update={"consumed": True})
            await self._committer.commit(self.name, EventType.DIRECTOR_SIGNAL_CONSUMED, sig.id, consumed)

    async def _remark(self, note: str) -> None:
        """Emit a short in-personality feed line as agent.remarked. No-op if note is empty."""
        if not note:
            return
        await self._committer.commit(
            self.name, EventType.AGENT_REMARKED, self.name, AgentRemark(agent_name=self.name, note=note)
        )

    async def _commit_thread_intents(
        self,
        intents: list[ThreadIntent],
        active_thread_ids: set[str],
        chapter_id: str = "",
        source: str = "declared",
    ) -> None:
        """Turn agent-declared ThreadIntent entries into thread.* commits.

        `plant` mints a new id via slugify_thread_name(intent.name) and is
        dropped only if the name is blank. `touch`/`pay_off`/`abandon` must
        cite an id present in `active_thread_ids` — the thread's known,
        non-terminal ids at poll time (see Author.poll/Editor.poll); an
        intent naming an unknown or already-terminal id is dropped with a
        logged warning and no event is committed. No-op on an empty list.
        """
        for intent in intents:
            if intent.action == "plant":
                if not intent.name.strip():
                    logger.warning("%s: dropped thread plant intent with empty name", self.name)
                    continue
                thread_id = slugify_thread_name(intent.name)
                if thread_id in active_thread_ids:
                    # A thread id is minted exactly once, at thread.planted. This
                    # plant collides with an id that's already live, so the agent
                    # clearly means "this thread is live" — downgrade to a touch
                    # instead of committing a planted event the projection would
                    # just no-op.
                    logger.info(
                        "%s: plant %r collides with active thread id %r, downgrading to touch",
                        self.name, intent.name, thread_id,
                    )
                    await self._committer.commit(
                        self.name, EventType.THREAD_TOUCHED, thread_id,
                        ThreadTouched(id=thread_id, chapter_id=chapter_id, note=intent.note, source=source),
                    )
                    continue
                logger.warning(
                    "%s: plant %r mints id %r; if this id already exists (terminal or unknown "
                    "to the caller) the commit will be a projection no-op",
                    self.name, intent.name, thread_id,
                )
                await self._committer.commit(
                    self.name, EventType.THREAD_PLANTED, thread_id,
                    ThreadPlanted(id=thread_id, name=intent.name, chapter_id=chapter_id, note=intent.note, source=source),
                )
                continue
            thread_id = _normalize_id(intent.id)
            if thread_id not in active_thread_ids:
                logger.warning(
                    "%s: dropped thread %s intent for unknown id %r", self.name, intent.action, intent.id
                )
                continue
            payload_cls, event_type = {
                "touch": (ThreadTouched, EventType.THREAD_TOUCHED),
                "pay_off": (ThreadPaidOff, EventType.THREAD_PAID_OFF),
                "abandon": (ThreadAbandoned, EventType.THREAD_ABANDONED),
            }[intent.action]
            if payload_cls is ThreadAbandoned:
                payload = payload_cls(id=thread_id, chapter_id=chapter_id, note=intent.note)
            else:
                payload = payload_cls(id=thread_id, chapter_id=chapter_id, note=intent.note, source=source)
            await self._committer.commit(self.name, event_type, thread_id, payload)

    async def _commit_theme_intents(
        self,
        intents: list[ThemeIntent],
        active_theme_ids: set[str],
        chapter_id: str = "",
        source: str = "declared",
        embedding_store=None,
    ) -> None:
        """Turn agent-declared ThemeIntent entries into theme.* commits.

        `introduce` mints a new id via slugify_theme_name(intent.title) and is
        dropped only if the title is blank. `develop` must cite an id present
        in `active_theme_ids` — an intent naming an unknown id is dropped
        with a logged warning and no event is committed. No-op on an empty
        list. Themes have no terminal state (M5.2 Locked decision 6).

        `embedding_store` is an optional `novelizer.store.embeddings.
        EmbeddingStore`; when provided, every successful `introduce` commit
        is upserted into its themes collection and checked for a near-
        duplicate via `novelizer.brain.theme_similarity.
        suggest_near_duplicate_theme`. A near-duplicate never blocks or
        merges the new theme.introduced commit -- it only files an Editor-
        facing `retcon_request.created`, tagged `THEME_SIMILARITY_SOURCE_TAG`,
        deduped against the open queue by description (same pattern as the
        Editor's voice-drift flags). When `embedding_store` is None (the
        default, and every existing call site that predates this), this is
        a complete no-op -- behavior is unchanged.
        """
        for intent in intents:
            if intent.action == "introduce":
                if not intent.title.strip():
                    logger.warning("%s: dropped theme introduce intent with empty title", self.name)
                    continue
                theme_id = slugify_theme_name(intent.title)
                if theme_id in active_theme_ids:
                    # A theme id is minted exactly once, at theme.introduced. This
                    # introduce collides with an id that's already live, so the agent
                    # clearly means "this theme is live" — downgrade to a develop
                    # instead of committing an introduced event the projection would
                    # just no-op.
                    logger.info(
                        "%s: introduce %r collides with active theme id %r, downgrading to develop",
                        self.name, intent.title, theme_id,
                    )
                    await self._committer.commit(
                        self.name, EventType.THEME_DEVELOPED, theme_id,
                        ThemeDeveloped(id=theme_id, chapter_id=chapter_id, note=intent.note, source=source),
                    )
                    continue
                logger.warning(
                    "%s: introduce %r mints id %r; if this id already exists (unknown to the "
                    "caller) the commit will be a projection no-op",
                    self.name, intent.title, theme_id,
                )
                if embedding_store is not None:
                    from novelizer.brain.theme_similarity import (
                        THEME_SIMILARITY_SOURCE_TAG, suggest_near_duplicate_theme,
                    )
                    from novelizer.store.models import ThemeRecord as _ThemeRecord
                    new_theme = _ThemeRecord(id=theme_id, title=intent.title)
                    duplicate_id = await suggest_near_duplicate_theme(embedding_store, new_theme)
                await self._committer.commit(
                    self.name, EventType.THEME_INTRODUCED, theme_id,
                    ThemeIntroduced(id=theme_id, title=intent.title, chapter_id=chapter_id, note=intent.note, source=source),
                )
                if embedding_store is not None:
                    await embedding_store.upsert_theme(new_theme)
                    if duplicate_id is not None:
                        existing = None
                        get_theme = getattr(self._read, "get_theme", None)
                        if get_theme is not None:
                            existing = await get_theme(duplicate_id)
                        existing_title = existing.title if existing is not None else duplicate_id
                        description = (
                            f"{THEME_SIMILARITY_SOURCE_TAG} theme '{theme_id}' ('{intent.title}') "
                            f"may duplicate existing theme '{duplicate_id}' ('{existing_title}')"
                        )
                        open_reqs = await self._read.list_retcon_requests(status=RetconStatus.open)
                        seen_descriptions = {r.description for r in open_reqs}
                        if description not in seen_descriptions:
                            req = RetconRequest(
                                description=description,
                                conflicting_entry_ids=[theme_id, duplicate_id],
                                proposed_resolution="",
                            )
                            await self._committer.commit(
                                self.name, EventType.RETCON_REQUEST_CREATED, req.id, req
                            )
                continue
            theme_id = _normalize_id(intent.id)
            if theme_id not in active_theme_ids:
                logger.warning(
                    "%s: dropped theme %s intent for unknown id %r", self.name, intent.action, intent.id
                )
                continue
            await self._committer.commit(
                self.name, EventType.THEME_DEVELOPED, theme_id,
                ThemeDeveloped(id=theme_id, chapter_id=chapter_id, note=intent.note, source=source),
            )

    async def _commit_knowledge_intents(
        self,
        intents: list[KnowledgeIntent],
        active_secret_ids: set[str],
        chapter_id: str = "",
        allowed_actions: frozenset[str] = frozenset({"plant", "learn", "reveal", "uses"}),
        source: str = "declared",
    ) -> None:
        """Turn agent-declared KnowledgeIntent entries into secret.* commits.

        `plant` mints a new id via slugify_secret_name(intent.title) and is
        dropped only if the title is blank; a plant colliding with an
        already-known active id is dropped with a warning -- secrets have no
        touch-analog the way threads do (M3.1's plant-collision downgrades
        to thread.touched, which needs only id+note; a colliding secret
        plant can't be safely reinterpreted as learn/reveal/uses without a
        character_id or reveal semantics the plant intent doesn't carry, so
        the safe choice is to drop it, matching how an unknown-id
        learn/reveal/uses intent is dropped below). `learn`/`reveal`/`uses`
        must cite an id present in `active_secret_ids`; `learn`/`uses`
        additionally require a non-blank `character_id`. `allowed_actions`
        restricts which actions this caller may commit -- CharacterKeeper
        passes frozenset({"learn"}) since minting/revealing a secret is a
        narrative-authoring act reserved for Author/Editor (Locked decision
        #1). Any intent whose action is not in `allowed_actions`, or that
        fails validation, is dropped with a logged warning and no event is
        committed. No-op on an empty list.
        """
        for intent in intents:
            if intent.action not in allowed_actions:
                logger.warning(
                    "%s: dropped knowledge intent action %r not permitted for this agent",
                    self.name, intent.action,
                )
                continue
            if intent.action == "plant":
                if not intent.title.strip():
                    logger.warning("%s: dropped secret plant intent with empty title", self.name)
                    continue
                secret_id = slugify_secret_name(intent.title)
                if secret_id in active_secret_ids:
                    logger.warning(
                        "%s: plant %r collides with existing secret id %r, dropping",
                        self.name, intent.title, secret_id,
                    )
                    continue
                await self._committer.commit(
                    self.name, EventType.SECRET_CREATED, secret_id,
                    SecretCreated(id=secret_id, title=intent.title, chapter_id=chapter_id, note=intent.note),
                )
                continue
            secret_id = _normalize_id(intent.id)
            if secret_id not in active_secret_ids:
                logger.warning(
                    "%s: dropped knowledge %s intent for unknown secret id %r", self.name, intent.action, intent.id
                )
                continue
            if intent.action in ("learn", "uses") and not intent.character_id.strip():
                logger.warning(
                    "%s: dropped knowledge %s intent with empty character_id", self.name, intent.action
                )
                continue
            event_type, payload_cls = _KNOWLEDGE_EVENT_BY_ACTION[intent.action]
            if intent.action == "reveal":
                payload = payload_cls(id=secret_id, chapter_id=chapter_id, note=intent.note)
            else:
                payload = payload_cls(
                    id=secret_id, character_id=_normalize_id(intent.character_id), chapter_id=chapter_id,
                    note=intent.note, source=source,
                )
            await self._committer.commit(self.name, event_type, secret_id, payload)

    async def _commit_causal_intents(
        self, intents: list[CausalIntent], valid_chapter_ids: set[str], source: str = "declared"
    ) -> None:
        """Turn agent-declared CausalIntent entries into
        causal_edge.declared commits.

        Both `cause_chapter_id` and `effect_chapter_id` must be present in
        `valid_chapter_ids`, or the intent is dropped with a logged warning;
        a self-edge (cause == effect) is dropped with a logged warning
        first, since Locked decision #4's ordering-violation check (M4.2)
        needs two distinct chapters to be meaningful. No deduplication: an
        edge has no minted identity and no lifecycle (Locked decision #4),
        so every valid declared edge is committed as its own fact even if
        identical to a prior commit -- the property test in this plan
        (Task 8) asserts replay never drops OR duplicates a declared edge,
        which requires a strict 1:1 event-to-row mapping, not a deduped one.
        No-op on an empty list.
        """
        for intent in intents:
            cause_id = _normalize_id(intent.cause_chapter_id)
            effect_id = _normalize_id(intent.effect_chapter_id)
            if cause_id == effect_id:
                logger.warning(
                    "%s: dropped self-edge causal intent for chapter %r", self.name, intent.cause_chapter_id
                )
                continue
            if cause_id not in valid_chapter_ids or effect_id not in valid_chapter_ids:
                logger.warning(
                    "%s: dropped causal intent citing unknown chapter id(s) %r -> %r",
                    self.name, intent.cause_chapter_id, intent.effect_chapter_id,
                )
                continue
            await self._committer.commit(
                self.name, EventType.CAUSAL_EDGE_DECLARED, effect_id,
                CausalEdgeDeclared(
                    cause_chapter_id=cause_id,
                    effect_chapter_id=effect_id,
                    note=intent.note,
                    source=source,
                ),
            )
