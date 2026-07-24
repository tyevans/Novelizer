from __future__ import annotations
import asyncio
import json
import logging
import os
from pathlib import Path
from novelizer.canon.events import EventType
from novelizer.store.drain import drain_pending
from novelizer.store.poison_ladder import PoisonLadder
from novelizer.text_chunk import chunk_prose

logger = logging.getLogger(__name__)

# Smaller than embeddings.py's 6000-char retrieval chunk size: here the
# failure mode is *output* length (entity/relation density per call), not
# input length, so a size tuned for retrieval granularity isn't known-safe
# for bounding structured-output size.
_EXTRACTION_CHUNK_CHARS = 3000
_EXTRACTION_CHUNK_OVERLAP = 200

INDEXED_EVENT_TYPES = [
    EventType.CHARACTER_CREATED, EventType.CHARACTER_UPDATED,
    EventType.WORLD_ENTRY_CREATED, EventType.WORLD_ENTRY_SUPERSEDED,
    EventType.CHAPTER_CREATED, EventType.CHAPTER_REVISED,
]


class KGProjector:
    """Event-cursor-driven knowledge-graph projector, structurally identical
    to store/indexer.py's CanonIndexer (same cursor-file contract, same
    failure-tolerant catch_up), except this one DOES write to world.db (via
    KGStore) as well as to the embeddings collection -- CanonIndexer's "never
    writes to world.db" rule doesn't apply here because the KG's tables live
    in world.db by design.

    Orphaned kg_entities rows (an entity whose only mention was cleared by a
    reflow) are left in place rather than garbage-collected: they're
    harmless (unreferenced by any embedding, so unreachable via search_canon)
    and pruning them is out of scope for this task -- YAGNI until an actual
    need (e.g. a Director-facing entity browser) shows up.
    """

    def __init__(self, events, read_store, kg_store, embedding_store, extraction_runner,
                 cursor_path: str, poison_skip_after: int = 3, pool=None,
                 drain_concurrency: int = 4) -> None:
        self._events = events
        self._read = read_store
        self._kg = kg_store
        self._emb = embedding_store
        self._runner = extraction_runner
        self._cursor_path = Path(cursor_path)
        self._poison = PoisonLadder(poison_skip_after)
        # Shared LLM/endpoint ceiling (duck-typed AdaptivePool) and the parallel
        # drain's fan-out cap, same as CanonIndexer (see store/drain.py).
        self._pool = pool
        self._drain_concurrency = drain_concurrency
        # Serializes the world.db read-modify-writes across concurrently draining
        # partitions -- the one thing that makes this drain differ from the canon
        # indexer's embed-only one (see _index_chapter). Built lazily so the
        # projector can be constructed outside a running loop, mirroring
        # AdaptivePool's Condition.
        self._commit_lock: asyncio.Lock | None = None

    def _load_cursor(self) -> int:
        try:
            return json.loads(self._cursor_path.read_text())["last_sequence"]
        except (OSError, ValueError, KeyError):
            return 0

    def _save_cursor(self, seq: int) -> None:
        tmp_path = self._cursor_path.with_suffix(self._cursor_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps({"last_sequence": seq}))
        os.replace(tmp_path, self._cursor_path)

    def _lock(self) -> asyncio.Lock:
        # Bind the commit lock to whatever loop first drains, exactly like
        # AdaptivePool binds its Condition -- the projector may be built before
        # the loop that runs it exists.
        if self._commit_lock is None:
            self._commit_lock = asyncio.Lock()
        return self._commit_lock

    async def lag(self) -> int:
        """Read-only: how many KG-relevant events haven't been extracted yet.
        Counts against this projector's own 6 event types, not the canon
        indexer's 24, so the two lags are not interchangeable. Never mutates
        the cursor and never calls _index_one."""
        last = self._load_cursor()
        return await self._events.count_since(last, event_types=list(INDEXED_EVENT_TYPES))

    async def catch_up(self) -> int:
        try:
            last = self._load_cursor()
            stored = await self._events.events_since(last, event_types=list(INDEXED_EVENT_TYPES))
            # Parallel drain (Phase 5), structurally identical to CanonIndexer's:
            # dedupe by aggregate, partition, drain concurrently under shared-pool
            # permits, advance the cursor over the longest contiguous success
            # prefix. The one difference lives in _index_chapter, where the
            # world.db write phase is serialized behind self._lock() while the LLM
            # extraction fans out -- see store/drain.py for the shared algorithm.
            return await drain_pending(
                stored,
                poison=self._poison,
                pool=self._pool,
                drain_concurrency=self._drain_concurrency,
                run_one=lambda ev: self._index_one(ev.event_type, ev.aggregate_id, ev.sequence),
                save_cursor=self._save_cursor,
                logger=logger,
                label="kg indexing",
            )
        except Exception as e:
            logger.warning("kg indexing catch_up failed (%s: %s); will retry next tick",
                            type(e).__name__, e)
            return 0

    async def _index_one(self, event_type: str, aggregate_id: str, seq: int) -> None:
        if event_type in (EventType.CHARACTER_CREATED, EventType.CHARACTER_UPDATED):
            await self._index_character(aggregate_id, seq)
        elif event_type in (EventType.WORLD_ENTRY_CREATED, EventType.WORLD_ENTRY_SUPERSEDED):
            await self._index_world_entry(aggregate_id, seq)
        elif event_type in (EventType.CHAPTER_CREATED, EventType.CHAPTER_REVISED):
            await self._index_chapter(aggregate_id, seq)

    async def _index_character(self, character_id: str, seq: int) -> None:
        from novelizer.store.kg_structured import facts_from_character
        char = await self._read.get_character(character_id)
        if char is None:
            return
        names = {c.id: c.name for c in await self._read.list_characters()}
        entities, relations = facts_from_character(char, character_names=names)
        # No LLM work to fan out here, but the world.db write still serializes
        # behind the commit lock so a concurrent chapter partition can't
        # interleave with this upsert_entity find-then-insert.
        async with self._lock():
            await self._commit_facts(entities, relations, fingerprint=f"character:{character_id}", seq=seq)

    async def _index_world_entry(self, entry_id: str, seq: int) -> None:
        from novelizer.store.kg_structured import facts_from_world_entry
        entries = {e.id: e for e in await self._read.list_world_entries()}
        entry = entries.get(entry_id)
        if entry is None:
            return
        entities, relations = facts_from_world_entry(entry)
        async with self._lock():
            await self._commit_facts(entities, relations, fingerprint=f"world_entry:{entry_id}", seq=seq)

    async def _index_chapter(self, chapter_id: str, seq: int) -> None:
        chapter = await self._read.get_chapter(chapter_id)
        if chapter is None:
            return
        fingerprint = f"chapter:{chapter_id}"
        # Extraction is the expensive LLM call and the whole reason the drain
        # goes parallel, so it runs OUTSIDE the commit lock: concurrent chapter
        # partitions extract at the same time under their shared pool permits.
        entities, relations = await self._extract_chapter_facts(chapter)

        # Everything below writes to world.db / chroma and runs UNDER the lock.
        # The hazard is upsert_entity's read-modify-write (find_entity_by_name
        # then INSERT): two chapters mentioning the same entity name would both
        # find it absent and double-insert, or lose an update, interleaving at
        # their await points even though aiosqlite serializes single statements.
        # Serializing the commit phase -- fast next to the LLM call -- closes
        # that race while extraction still fans out.
        async with self._lock():
            # Reflow: clear this chapter's prior prose-extracted mentions before
            # re-extracting, so a revision that drops a detail also drops its
            # entity's searchability (see class docstring on orphan rows).
            cleared_relation_ids = await self._kg.clear_relation_mentions_for_fingerprint(fingerprint)
            for relation_id in cleared_relation_ids:
                if not await self._kg.relation_has_mentions(relation_id):
                    await self._kg.delete_relation(relation_id)

            cleared_entity_ids = await self._kg.clear_mentions_for_fingerprint(fingerprint)
            for entity_id in cleared_entity_ids:
                remaining = await self._kg.entity_relations(entity_id)
                still_mentioned = await self._kg.has_mentions(entity_id)
                if not remaining and not still_mentioned:
                    await self._emb.delete_entity(str(entity_id))

            if not entities and not relations:
                return

            canon_ids_by_name = {
                c.name.lower(): c.id for c in await self._read.list_characters()
            }
            canon_ids_by_name.update({
                e.title.lower(): e.id for e in await self._read.list_world_entries()
            })

            name_to_id: dict[str, int] = {}
            for entity in entities:
                entity_id = await self._kg.upsert_entity(
                    entity.name, entity.entity_type, entity.description,
                    canon_id=canon_ids_by_name.get(entity.name.lower()), seq=seq,
                )
                name_to_id[entity.name] = entity_id
                await self._kg.link_mention(entity_id, fingerprint)
                detail = self._format_entity_detail(entity.name, entity.entity_type, entity.description, [])
                await self._emb.upsert_entity(str(entity_id), entity.name, detail)

            for relation in relations:
                source_id = name_to_id.get(relation.source)
                target_id = name_to_id.get(relation.target)
                if source_id is None or target_id is None:
                    continue
                relation_id = await self._kg.upsert_relation(
                    source_id, target_id, relation.relation_type, seq=seq
                )
                await self._kg.link_relation_mention(relation_id, fingerprint)

    async def _extract_chapter_facts(self, chapter):
        # Whole-chapter prose in one extraction call risks the structured
        # JSON response overrunning llm_max_tokens on entity/relation-dense
        # chapters, truncating mid-JSON and stalling the indexer at that
        # event forever (seq 33 incident). Chunk the prose instead and merge
        # each chunk's facts; the overlap window can yield duplicate
        # entities/relations across chunk boundaries, so dedupe on merge.
        from novelizer.agents.kg_extraction import kg_extraction_prompt
        chunks = chunk_prose(chapter.prose, _EXTRACTION_CHUNK_CHARS, _EXTRACTION_CHUNK_OVERLAP)

        entities_by_name: dict[str, object] = {}
        relations_seen: dict[tuple[str, str, str], object] = {}
        for chunk in chunks:
            prompt = kg_extraction_prompt(chapter.title, chunk)
            result = await self._runner.ainvoke({"messages": [{"role": "user", "content": prompt}]})
            out = result.get("structured_response")
            if out is None:
                continue
            for entity in out.entities:
                key = entity.name.lower()
                existing = entities_by_name.get(key)
                if existing is None or (not existing.description and entity.description):
                    entities_by_name[key] = entity
            for relation in out.relations:
                key = (relation.source, relation.target, relation.relation_type)
                relations_seen.setdefault(key, relation)

        return list(entities_by_name.values()), list(relations_seen.values())

    @staticmethod
    def _format_entity_detail(name: str, entity_type: str, description: str, relations: list[dict]) -> str:
        rel_text = ", ".join(f"{r['relation_type']} {r['other_name']}" for r in relations)
        base = f"{name} [{entity_type}] {description}".strip()
        return f"{base} Relations: {rel_text}" if rel_text else base

    async def _commit_facts(self, entities, relations, fingerprint: str, seq: int) -> None:
        name_to_id: dict[str, int] = {}
        for fact in entities:
            entity_id = await self._kg.upsert_entity(
                fact.name, fact.entity_type, fact.description, canon_id=fact.canon_id, seq=seq,
            )
            name_to_id[fact.name] = entity_id
            await self._kg.link_mention(entity_id, fingerprint)
            existing_relations = await self._kg.entity_relations(entity_id)
            detail = self._format_entity_detail(fact.name, fact.entity_type, fact.description, existing_relations)
            await self._emb.upsert_entity(str(entity_id), fact.name, detail)
        for rel in relations:
            source_id = name_to_id.get(rel.source_name)
            if source_id is None:
                continue
            target = await self._kg.find_entity_by_name(rel.target_name, rel.target_type)
            if target is None:
                continue
            relation_id = await self._kg.upsert_relation(
                source_id, target["id"], rel.relation_type, seq=seq
            )
            await self._kg.link_relation_mention(relation_id, fingerprint)
