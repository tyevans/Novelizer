from __future__ import annotations
import logging
from novelizer.canon.events import (
    EventType, ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned,
    SecretCreated, SecretLearned, SecretReferenced, SecretRevealed, CausalEdgeDeclared,
    ThemeIntroduced, ThemeDeveloped,
    PromiseMade, PromiseProgressed, PromisePaid, PromiseReleased,
)
from novelizer.canon.threads import slugify_thread_name
from novelizer.canon.secrets import slugify_secret_name
from novelizer.canon.themes import slugify_theme_name
from novelizer.canon.promises import slugify_promise_name
from novelizer.agents.schemas import ThreadIntent, KnowledgeIntent, CausalIntent, ThemeIntent, PromiseIntent
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


async def commit_thread_intents(
    committer,
    agent_name: str,
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
                logger.warning("%s: dropped thread plant intent with empty name", agent_name)
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
                    agent_name, intent.name, thread_id,
                )
                await committer.commit(
                    agent_name, EventType.THREAD_TOUCHED, thread_id,
                    ThreadTouched(id=thread_id, chapter_id=chapter_id, note=intent.note, source=source),
                )
                continue
            logger.warning(
                "%s: plant %r mints id %r; if this id already exists (terminal or unknown "
                "to the caller) the commit will be a projection no-op",
                agent_name, intent.name, thread_id,
            )
            await committer.commit(
                agent_name, EventType.THREAD_PLANTED, thread_id,
                ThreadPlanted(id=thread_id, name=intent.name, chapter_id=chapter_id, note=intent.note, source=source),
            )
            continue
        thread_id = _normalize_id(intent.id)
        if thread_id not in active_thread_ids:
            logger.warning(
                "%s: dropped thread %s intent for unknown id %r", agent_name, intent.action, intent.id
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
        await committer.commit(agent_name, event_type, thread_id, payload)


async def commit_theme_intents(
    committer,
    agent_name: str,
    intents: list[ThemeIntent],
    active_theme_ids: set[str],
    chapter_id: str = "",
    source: str = "declared",
    embedding_store=None,
    read_store=None,
) -> None:
    """Turn agent-declared ThemeIntent entries into theme.* commits.

    `introduce` mints a new id via slugify_theme_name(intent.title) and is
    dropped only if the title is blank. `develop` must cite an id present
    in `active_theme_ids` — an intent naming an unknown id is dropped
    with a logged warning and no event is committed. No-op on an empty
    list. Themes have no terminal state (M5.2 Locked decision 6).

    `embedding_store` is an optional `novelizer.store.embeddings.
    EmbeddingStore`; when provided (together with `read_store`), every
    successful `introduce` commit is upserted into its themes collection
    and checked for a near-duplicate via `novelizer.brain.theme_similarity.
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
                logger.warning("%s: dropped theme introduce intent with empty title", agent_name)
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
                    agent_name, intent.title, theme_id,
                )
                await committer.commit(
                    agent_name, EventType.THEME_DEVELOPED, theme_id,
                    ThemeDeveloped(id=theme_id, chapter_id=chapter_id, note=intent.note, source=source),
                )
                continue
            logger.warning(
                "%s: introduce %r mints id %r; if this id already exists (unknown to the "
                "caller) the commit will be a projection no-op",
                agent_name, intent.title, theme_id,
            )
            if embedding_store is not None:
                from novelizer.brain.theme_similarity import (
                    THEME_SIMILARITY_SOURCE_TAG, suggest_near_duplicate_theme,
                )
                from novelizer.store.models import ThemeRecord as _ThemeRecord
                new_theme = _ThemeRecord(id=theme_id, title=intent.title)
                duplicate_id = await suggest_near_duplicate_theme(embedding_store, new_theme)
            await committer.commit(
                agent_name, EventType.THEME_INTRODUCED, theme_id,
                ThemeIntroduced(id=theme_id, title=intent.title, chapter_id=chapter_id, note=intent.note, source=source),
            )
            if embedding_store is not None:
                await embedding_store.upsert_theme(new_theme)
                if duplicate_id is not None:
                    existing = None
                    get_theme = getattr(read_store, "get_theme", None)
                    if get_theme is not None:
                        existing = await get_theme(duplicate_id)
                    existing_title = existing.title if existing is not None else duplicate_id
                    description = (
                        f"{THEME_SIMILARITY_SOURCE_TAG} theme '{theme_id}' ('{intent.title}') "
                        f"may duplicate existing theme '{duplicate_id}' ('{existing_title}')"
                    )
                    open_reqs = await read_store.list_retcon_requests(status=RetconStatus.open)
                    seen_descriptions = {r.description for r in open_reqs}
                    if description not in seen_descriptions:
                        req = RetconRequest(
                            description=description,
                            conflicting_entry_ids=[theme_id, duplicate_id],
                            proposed_resolution="",
                        )
                        await committer.commit(
                            agent_name, EventType.RETCON_REQUEST_CREATED, req.id, req
                        )
            continue
        theme_id = _normalize_id(intent.id)
        if theme_id not in active_theme_ids:
            logger.warning(
                "%s: dropped theme %s intent for unknown id %r", agent_name, intent.action, intent.id
            )
            continue
        await committer.commit(
            agent_name, EventType.THEME_DEVELOPED, theme_id,
            ThemeDeveloped(id=theme_id, chapter_id=chapter_id, note=intent.note, source=source),
        )


async def commit_knowledge_intents(
    committer,
    agent_name: str,
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
                agent_name, intent.action,
            )
            continue
        if intent.action == "plant":
            if not intent.title.strip():
                logger.warning("%s: dropped secret plant intent with empty title", agent_name)
                continue
            secret_id = slugify_secret_name(intent.title)
            if secret_id in active_secret_ids:
                logger.warning(
                    "%s: plant %r collides with existing secret id %r, dropping",
                    agent_name, intent.title, secret_id,
                )
                continue
            await committer.commit(
                agent_name, EventType.SECRET_CREATED, secret_id,
                SecretCreated(id=secret_id, title=intent.title, chapter_id=chapter_id, note=intent.note),
            )
            continue
        secret_id = _normalize_id(intent.id)
        if secret_id not in active_secret_ids:
            logger.warning(
                "%s: dropped knowledge %s intent for unknown secret id %r", agent_name, intent.action, intent.id
            )
            continue
        if intent.action in ("learn", "uses") and not intent.character_id.strip():
            logger.warning(
                "%s: dropped knowledge %s intent with empty character_id", agent_name, intent.action
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
        await committer.commit(agent_name, event_type, secret_id, payload)


async def commit_causal_intents(
    committer,
    agent_name: str,
    intents: list[CausalIntent],
    valid_chapter_ids: set[str],
    source: str = "declared",
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
                "%s: dropped self-edge causal intent for chapter %r", agent_name, intent.cause_chapter_id
            )
            continue
        if cause_id not in valid_chapter_ids or effect_id not in valid_chapter_ids:
            logger.warning(
                "%s: dropped causal intent citing unknown chapter id(s) %r -> %r",
                agent_name, intent.cause_chapter_id, intent.effect_chapter_id,
            )
            continue
        await committer.commit(
            agent_name, EventType.CAUSAL_EDGE_DECLARED, effect_id,
            CausalEdgeDeclared(
                cause_chapter_id=cause_id,
                effect_chapter_id=effect_id,
                note=intent.note,
                source=source,
            ),
        )


_PROMISE_EVENT_BY_ACTION = {
    "progress": (PromiseProgressed, EventType.PROMISE_PROGRESSED),
    "pay": (PromisePaid, EventType.PROMISE_PAID),
    "release": (PromiseReleased, EventType.PROMISE_RELEASED),
}


async def commit_promise_intents(
    committer,
    agent_name: str,
    intents: list[PromiseIntent],
    active_promise_ids: set[str],
    active_thread_ids: set[str],
    chapter_id: str = "",
    source: str = "declared",
) -> None:
    """Turn agent-declared PromiseIntent entries into promise.* commits.

    `make` mints a new id via slugify_promise_name(intent.name) and is
    dropped only if the name is blank; a make colliding with an id
    already in `active_promise_ids` downgrades to promise.progressed
    (same pattern as thread plant collisions). `make` may optionally
    cite a `thread_id`; a thread_id not present in `active_thread_ids`
    is dropped (logged) but does not block the promise commit.
    `progress`/`pay`/`release` must cite an id present in
    `active_promise_ids` -- an intent naming an unknown or already-
    terminal id is dropped with a logged warning and no event is
    committed. No-op on an empty list.
    """
    for intent in intents:
        if intent.action == "make":
            if not intent.name.strip():
                logger.warning("%s: dropped promise make with blank name", agent_name)
                continue
            promise_id = slugify_promise_name(intent.name)
            if promise_id in active_promise_ids:
                await committer.commit(
                    agent_name, EventType.PROMISE_PROGRESSED, promise_id,
                    PromiseProgressed(id=promise_id, chapter_id=chapter_id, note=intent.note, source=source),
                )
                continue
            thread_id = _normalize_id(intent.thread_id)
            if thread_id and thread_id not in active_thread_ids:
                logger.warning(
                    "%s: promise '%s' cited unknown thread %r — link dropped", agent_name, promise_id, thread_id
                )
                thread_id = ""
            await committer.commit(
                agent_name, EventType.PROMISE_MADE, promise_id,
                PromiseMade(
                    id=promise_id, name=intent.name.strip(), description=intent.description,
                    kind=intent.kind, chapter_id=chapter_id, thread_id=thread_id,
                    note=intent.note, source=source,
                ),
            )
            continue
        promise_id = _normalize_id(intent.id)
        if promise_id not in active_promise_ids:
            logger.warning(
                "%s: dropped promise %s citing unknown/terminal id %r", agent_name, intent.action, intent.id
            )
            continue
        payload_cls, event_type = _PROMISE_EVENT_BY_ACTION[intent.action]
        if payload_cls is PromiseReleased:
            payload = PromiseReleased(id=promise_id, reason=intent.note, chapter_id=chapter_id, source=source)
        else:
            payload = payload_cls(id=promise_id, chapter_id=chapter_id, note=intent.note, source=source)
        await committer.commit(agent_name, event_type, promise_id, payload)
