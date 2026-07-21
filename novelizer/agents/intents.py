from __future__ import annotations
import logging
import uuid
from novelizer.canon.events import (
    EventType, ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned,
    SecretCreated, SecretLearned, SecretReferenced, SecretRevealed, CausalEdgeDeclared,
    ThemeIntroduced, ThemeDeveloped,
    PromiseMade, PromiseProgressed, PromisePaid, PromiseReleased,
    BeatSpec, BlueprintAdopted, BlueprintRetargeted, BeatFulfilled, ChapterBriefDrafted, ChapterBriefSuperseded,
    ThreadResolutionPlanned, SecretRevealPlanned,
    ArcDeclared, ArcPivotPlanned, ArcAdvanced, ArcResolved,
)
from novelizer.canon.threads import slugify_thread_name
from novelizer.canon.secrets import slugify_secret_name
from novelizer.canon.themes import slugify_theme_name
from novelizer.canon.promises import slugify_promise_name
from novelizer.canon.beat_templates import BEAT_TEMPLATES
from novelizer.agents.schemas import (
    ThreadIntent, KnowledgeIntent, CausalIntent, ThemeIntent, PromiseIntent,
    BlueprintPlan, RetargetIntent, BriefIntent, BeatIntent, ResolutionPlanIntent, ArcIntent,
)
from novelizer.store.models import Flag, FlagStatus, ChapterBriefRecord

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


def _warn_if_ungrounded(agent_name: str, family: str, action: str, entity_id: str, evidence: str) -> None:
    """Log a citing action that names no supporting canon.

    Deliberately a warning, not a drop: an under-cited but real narrative beat
    is worth more than the silence of discarding it, and the warning rate is
    the measurement that would justify a stricter policy later.
    """
    if evidence.strip():
        return
    logger.warning(
        "%s: %s %s intent for %r carries no evidence — the claim cites canon the "
        "agent may not have read",
        agent_name, family, action, entity_id,
    )


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
        _warn_if_ungrounded(agent_name, "thread", intent.action, thread_id, intent.evidence)
        payload_cls, event_type = {
            "touch": (ThreadTouched, EventType.THREAD_TOUCHED),
            "pay_off": (ThreadPaidOff, EventType.THREAD_PAID_OFF),
            "abandon": (ThreadAbandoned, EventType.THREAD_ABANDONED),
        }[intent.action]
        if payload_cls is ThreadAbandoned:
            payload = payload_cls(id=thread_id, chapter_id=chapter_id, note=intent.note,
                                  evidence=intent.evidence)
        else:
            payload = payload_cls(id=thread_id, chapter_id=chapter_id, note=intent.note, source=source,
                                  evidence=intent.evidence)
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
                    open_reqs = await read_store.list_flags(category="thematic", status=FlagStatus.open)
                    seen_descriptions = {r.description for r in open_reqs}
                    if description not in seen_descriptions:
                        req = Flag(
                            category="thematic",
                            description=description,
                            related_entry_ids=[theme_id, duplicate_id],
                            proposed_resolution="",
                        )
                        await committer.commit(
                            agent_name, EventType.FLAG_CREATED, req.id, req
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
        _warn_if_ungrounded(agent_name, "knowledge", intent.action, secret_id, intent.evidence)
        event_type, payload_cls = _KNOWLEDGE_EVENT_BY_ACTION[intent.action]
        if intent.action == "reveal":
            payload = payload_cls(id=secret_id, chapter_id=chapter_id, note=intent.note,
                                  evidence=intent.evidence)
        else:
            payload = payload_cls(
                id=secret_id, character_id=_normalize_id(intent.character_id), chapter_id=chapter_id,
                note=intent.note, source=source, evidence=intent.evidence,
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
        _warn_if_ungrounded(
            agent_name, "causal", "declare", f"{cause_id}->{effect_id}", intent.evidence
        )
        await committer.commit(
            agent_name, EventType.CAUSAL_EDGE_DECLARED, effect_id,
            CausalEdgeDeclared(
                cause_chapter_id=cause_id,
                effect_chapter_id=effect_id,
                note=intent.note,
                source=source,
                evidence=intent.evidence,
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
    committed. `make` may also optionally carry a target payoff window
    (`window_lo`/`window_hi`, 1-based chapter ordinals); an invalid
    window (not `lo==hi==0` and not `1 <= lo <= hi`) is dropped with a
    logged warning and zeroed rather than blocking the commit -- an
    invalid window must never enter canon. No-op on an empty list.
    """
    for intent in intents:
        if intent.action == "make":
            if not intent.name.strip():
                logger.warning("%s: dropped promise make with blank name", agent_name)
                continue
            promise_id = slugify_promise_name(intent.name)
            if promise_id in active_promise_ids:
                # A promise id is minted exactly once, at promise.made. This
                # make collides with an id that's already live, so the agent
                # clearly means "this promise is live" — downgrade to a
                # progress instead of committing a made event the projection
                # would just no-op.
                logger.info(
                    "%s: make %r collides with active promise id %r, downgrading to progress",
                    agent_name, intent.name, promise_id,
                )
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
            window_lo, window_hi = intent.window_lo, intent.window_hi
            if not (window_lo == window_hi == 0 or 1 <= window_lo <= window_hi):
                logger.warning(
                    "%s: promise '%s' declared invalid window %r-%r, zeroing",
                    agent_name, promise_id, window_lo, window_hi,
                )
                window_lo, window_hi = 0, 0
            logger.warning(
                "%s: make %r mints id %r; if this id already exists (unknown to the "
                "caller) the commit will be a projection no-op",
                agent_name, intent.name, promise_id,
            )
            await committer.commit(
                agent_name, EventType.PROMISE_MADE, promise_id,
                PromiseMade(
                    id=promise_id, name=intent.name.strip(), description=intent.description,
                    kind=intent.kind, chapter_id=chapter_id, thread_id=thread_id,
                    window_lo=window_lo, window_hi=window_hi,
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


async def commit_blueprint_plan(committer, agent_name: str, plan: BlueprintPlan | None) -> None:
    """Turn an agent-declared BlueprintPlan into a blueprint.adopted commit.

    None is a no-op. `framework` must name a built-in template
    (canon/beat_templates.BEAT_TEMPLATES); an unknown framework is dropped
    with a logged warning. `target_chapter_count` below 3 is dropped with a
    logged warning -- too few chapters to place a beat sequence
    meaningfully. On the happy path, mints `blueprint_id` (uuid) and a
    BeatSpec for every TemplateBeat in the framework's template, with
    `beat_id = f"{blueprint_id}-{template_beat.slug}"`. blueprint.adopted is
    always routed through the gated commit path by policy.py, regardless
    of this helper's caller.
    """
    if plan is None:
        return
    if plan.framework not in BEAT_TEMPLATES:
        logger.warning(
            "%s: dropped blueprint plan citing unknown framework %r", agent_name, plan.framework
        )
        return
    if plan.target_chapter_count < 3:
        logger.warning(
            "%s: dropped blueprint plan with target_chapter_count %r (< 3)",
            agent_name, plan.target_chapter_count,
        )
        return
    payload = mint_blueprint(
        plan.framework, plan.target_chapter_count, plan.genre,
        obligatory_scenes=list(plan.obligatory_scenes), note=plan.note,
    )
    await committer.commit(agent_name, EventType.BLUEPRINT_ADOPTED, payload.blueprint_id, payload)


def mint_blueprint(
    framework: str, target_chapter_count: int, genre: str = "",
    obligatory_scenes: list[str] | None = None, note: str = "",
) -> BlueprintAdopted:
    """Mint a fresh BlueprintAdopted payload: a new blueprint_id and a BeatSpec
    for every TemplateBeat in the framework's template, with
    `beat_id = f"{blueprint_id}-{template_beat.slug}"`. Caller validates
    `framework` and `target_chapter_count` -- this assumes both are already
    sound (KeyError on an unknown framework)."""
    blueprint_id = str(uuid.uuid4())
    beats = [
        BeatSpec(
            beat_id=f"{blueprint_id}-{template_beat.slug}",
            slug=template_beat.slug,
            name=template_beat.name,
            ideal_pct=template_beat.ideal_pct,
            tolerance_pct=template_beat.tolerance_pct,
            expected_polarity=template_beat.expected_polarity,
        )
        for template_beat in BEAT_TEMPLATES[framework]
    ]
    return BlueprintAdopted(
        blueprint_id=blueprint_id, framework=framework,
        target_chapter_count=target_chapter_count, genre=genre,
        beats=beats, obligatory_scenes=list(obligatory_scenes or []), note=note,
    )


async def commit_retarget_intent(
    committer, agent_name: str, intent: RetargetIntent | None, blueprint,
) -> None:
    """Turn an agent-declared RetargetIntent into a blueprint.retargeted commit.

    None is a no-op, as is the absence of an active blueprint. Dropped with
    a logged warning when target_chapter_count < 3 (too few chapters to
    place a beat sequence meaningfully -- mirrors commit_blueprint_plan's
    guard), or when it equals the blueprint's current target_chapter_count
    (the no-change guard against churn). blueprint.retargeted is
    _NEVER_GATED (see canon/policy.py), so this commits directly regardless
    of autonomy level.
    """
    if intent is None:
        return
    if blueprint is None:
        logger.warning(
            "%s: dropped retarget intent (target_chapter_count=%r) -- no active blueprint",
            agent_name, intent.target_chapter_count,
        )
        return
    if intent.target_chapter_count < 3:
        logger.warning(
            "%s: dropped retarget intent with target_chapter_count %r (< 3)",
            agent_name, intent.target_chapter_count,
        )
        return
    if intent.target_chapter_count == blueprint.target_chapter_count:
        logger.warning(
            "%s: dropped retarget intent -- target_chapter_count %r matches the current blueprint",
            agent_name, intent.target_chapter_count,
        )
        return
    await committer.commit(
        agent_name, EventType.BLUEPRINT_RETARGETED, blueprint.id,
        BlueprintRetargeted(blueprint_id=blueprint.id, target_chapter_count=intent.target_chapter_count),
    )


async def commit_brief_intents(
    committer,
    agent_name: str,
    intents: list[BriefIntent],
    open_brief_ids: list[ChapterBriefRecord],
    drafted_chapter_count: int,
    active_thread_ids: set[str],
    active_beat_ids: set[str],
    active_promise_ids: set[str],
) -> None:
    """Turn agent-declared BriefIntent entries into chapter_brief.* commits.

    `draft`: `target_ordinal` must exceed `drafted_chapter_count` and be
    positive -- plan the future, not the past or present; a violating
    ordinal is dropped with a logged warning. A blank `goal` is dropped
    with a logged warning. Cited thread/beat/promise ids are filtered
    per-list against the active sets (unknown ids dropped from that list
    with one logged warning; the brief itself is kept). If `open_brief_ids`
    (the caller's current open briefs) already has one targeting the same
    ordinal, that brief is superseded first (chapter_brief.superseded,
    superseded_by_brief_id = the new brief's id) before the new brief's
    chapter_brief.drafted commits -- one open brief per ordinal is a
    helper-enforced invariant. `brief_id` mints via `str(uuid.uuid4())`.

    `supersede`: `id` must be present among `open_brief_ids`' ids, else
    dropped with a logged warning; commits chapter_brief.superseded with
    `superseded_by_brief_id=""` (nothing replaces it).

    No-op on an empty list.
    """
    open_by_id = {b.id: b for b in open_brief_ids}
    open_by_ordinal = {b.target_ordinal: b for b in open_brief_ids}
    for intent in intents:
        if intent.action == "draft":
            if intent.target_ordinal <= 0 or intent.target_ordinal <= drafted_chapter_count:
                logger.warning(
                    "%s: dropped brief draft for target_ordinal %r (drafted_chapter_count=%r)",
                    agent_name, intent.target_ordinal, drafted_chapter_count,
                )
                continue
            if not intent.goal.strip():
                logger.warning("%s: dropped brief draft with blank goal", agent_name)
                continue
            threads_to_touch = [
                _normalize_id(t) for t in intent.threads_to_touch if _normalize_id(t) in active_thread_ids
            ]
            beats_to_hit = [
                _normalize_id(b) for b in intent.beats_to_hit if _normalize_id(b) in active_beat_ids
            ]
            promises_to_progress = [
                _normalize_id(p) for p in intent.promises_to_progress if _normalize_id(p) in active_promise_ids
            ]
            if (
                len(threads_to_touch) != len(intent.threads_to_touch)
                or len(beats_to_hit) != len(intent.beats_to_hit)
                or len(promises_to_progress) != len(intent.promises_to_progress)
            ):
                logger.warning(
                    "%s: brief draft for ordinal %r cited unknown thread/beat/promise id(s), dropped from lists",
                    agent_name, intent.target_ordinal,
                )
            brief_id = str(uuid.uuid4())
            existing = open_by_ordinal.get(intent.target_ordinal)
            if existing is not None:
                await committer.commit(
                    agent_name, EventType.CHAPTER_BRIEF_SUPERSEDED, existing.id,
                    ChapterBriefSuperseded(brief_id=existing.id, superseded_by_brief_id=brief_id),
                )
            await committer.commit(
                agent_name, EventType.CHAPTER_BRIEF_DRAFTED, brief_id,
                ChapterBriefDrafted(
                    brief_id=brief_id, target_ordinal=intent.target_ordinal, goal=intent.goal,
                    pov_character_id=intent.pov_character_id,
                    threads_to_touch=threads_to_touch, beats_to_hit=beats_to_hit,
                    promises_to_progress=promises_to_progress,
                    value_shift=intent.value_shift, planned_outcome=intent.planned_outcome,
                    synopsis=intent.synopsis,
                ),
            )
            open_by_ordinal[intent.target_ordinal] = ChapterBriefRecord(
                id=brief_id, target_ordinal=intent.target_ordinal, goal=intent.goal,
            )
            continue
        # supersede
        brief_id = _normalize_id(intent.id)
        if brief_id not in open_by_id:
            logger.warning(
                "%s: dropped brief supersede citing unknown/non-open id %r", agent_name, intent.id
            )
            continue
        await committer.commit(
            agent_name, EventType.CHAPTER_BRIEF_SUPERSEDED, brief_id,
            ChapterBriefSuperseded(brief_id=brief_id, superseded_by_brief_id=""),
        )


async def commit_beat_intents(
    committer,
    agent_name: str,
    intents: list[BeatIntent],
    active_beat_ids: set[str],
    valid_chapter_ids: set[str],
) -> None:
    """Turn agent-declared BeatIntent entries into beat.fulfilled commits.

    `beat_id` not present in `active_beat_ids` is dropped with a logged
    warning. A non-blank `chapter_id` not present in `valid_chapter_ids`
    is dropped with a logged warning (a blank `chapter_id` clears a prior
    fulfillment and is always valid). No-op on an empty list.
    """
    for intent in intents:
        beat_id = _normalize_id(intent.beat_id)
        if beat_id not in active_beat_ids:
            logger.warning(
                "%s: dropped beat fulfill citing unknown beat id %r", agent_name, intent.beat_id
            )
            continue
        chapter_id = _normalize_id(intent.chapter_id)
        if chapter_id and chapter_id not in valid_chapter_ids:
            logger.warning(
                "%s: dropped beat fulfill citing unknown chapter id %r", agent_name, intent.chapter_id
            )
            continue
        await committer.commit(
            agent_name, EventType.BEAT_FULFILLED, beat_id,
            BeatFulfilled(beat_id=beat_id, chapter_id=chapter_id, note=intent.note),
        )


async def commit_resolution_plan_intents(
    committer,
    agent_name: str,
    intents: list[ResolutionPlanIntent],
    active_thread_ids: set[str],
    unrevealed_secret_ids: set[str],
) -> None:
    """Turn agent-declared ResolutionPlanIntent entries into
    thread.resolution_planned / secret.reveal_planned commits.

    An invalid window (not `lo==hi==0` and not `1 <= lo <= hi`) is dropped
    with a logged warning -- an invalid window must never enter canon. An
    id not present in the relevant active set (`active_thread_ids` for
    kind="thread", `unrevealed_secret_ids` for kind="secret") is dropped
    with a logged warning. No-op on an empty list.
    """
    for intent in intents:
        if not (intent.window_lo == intent.window_hi == 0 or 1 <= intent.window_lo <= intent.window_hi):
            logger.warning(
                "%s: dropped resolution plan for %r citing invalid window %r-%r",
                agent_name, intent.id, intent.window_lo, intent.window_hi,
            )
            continue
        target_id = _normalize_id(intent.id)
        if intent.kind == "thread":
            if target_id not in active_thread_ids:
                logger.warning(
                    "%s: dropped thread resolution plan citing unknown id %r", agent_name, intent.id
                )
                continue
            await committer.commit(
                agent_name, EventType.THREAD_RESOLUTION_PLANNED, target_id,
                ThreadResolutionPlanned(
                    id=target_id, window_lo=intent.window_lo, window_hi=intent.window_hi,
                    planned_payoff_note=intent.note,
                ),
            )
        else:
            if target_id not in unrevealed_secret_ids:
                logger.warning(
                    "%s: dropped secret reveal plan citing unknown id %r", agent_name, intent.id
                )
                continue
            await committer.commit(
                agent_name, EventType.SECRET_REVEAL_PLANNED, target_id,
                SecretRevealPlanned(id=target_id, window_lo=intent.window_lo, window_hi=intent.window_hi),
            )


async def commit_arc_intents(
    committer,
    agent_name: str,
    intents: list[ArcIntent],
    active_arc_ids: set[str],
    character_ids: set[str],
    active_beat_ids: set[str],
    chapter_id: str = "",
) -> None:
    """Turn agent-declared ArcIntent entries into arc.* commits.

    `declare` mints a new id via uuid.uuid4() and requires a `character_id`
    present in `character_ids` (unknown character dropped, logged) and a
    non-blank `arc_type` (blank dropped, logged). `plan_pivot` must cite an
    id in `active_arc_ids` AND a `beat_id` in `active_beat_ids` -- either
    missing drops the intent (logged), no event committed. `advance` must
    cite an id in `active_arc_ids`; the commit carries `chapter_id`.
    `resolve` must cite an id in `active_arc_ids` and a non-blank `outcome`;
    either missing drops the intent (logged). No-op on an empty list.
    """
    for intent in intents:
        if intent.action == "declare":
            character_id = _normalize_id(intent.character_id)
            if character_id not in character_ids:
                logger.warning(
                    "%s: dropped arc declare citing unknown character %r", agent_name, intent.character_id
                )
                continue
            if not intent.arc_type:
                logger.warning(
                    "%s: dropped arc declare for %r with blank arc_type", agent_name, character_id
                )
                continue
            arc_id = str(uuid.uuid4())
            await committer.commit(
                agent_name, EventType.ARC_DECLARED, arc_id,
                ArcDeclared(
                    arc_id=arc_id, character_id=character_id, arc_type=intent.arc_type,
                    ghost=intent.ghost, lie=intent.lie, truth=intent.truth,
                    want=intent.want, need=intent.need, note=intent.note,
                ),
            )
            continue
        arc_id = _normalize_id(intent.id)
        if arc_id not in active_arc_ids:
            logger.warning(
                "%s: dropped arc %s citing unknown/inactive id %r", agent_name, intent.action, intent.id
            )
            continue
        if intent.action == "plan_pivot":
            beat_id = _normalize_id(intent.beat_id)
            if beat_id not in active_beat_ids:
                logger.warning(
                    "%s: dropped arc pivot for %r citing unknown beat %r", agent_name, arc_id, intent.beat_id
                )
                continue
            await committer.commit(
                agent_name, EventType.ARC_PIVOT_PLANNED, arc_id,
                ArcPivotPlanned(arc_id=arc_id, beat_id=beat_id, description=intent.note),
            )
        elif intent.action == "advance":
            await committer.commit(
                agent_name, EventType.ARC_ADVANCED, arc_id,
                ArcAdvanced(arc_id=arc_id, chapter_id=chapter_id, note=intent.note),
            )
        elif intent.action == "resolve":
            if not intent.outcome:
                logger.warning(
                    "%s: dropped arc resolve for %r with blank outcome", agent_name, arc_id
                )
                continue
            await committer.commit(
                agent_name, EventType.ARC_RESOLVED, arc_id,
                ArcResolved(arc_id=arc_id, chapter_id=chapter_id, outcome=intent.outcome, note=intent.note),
            )
