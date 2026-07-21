from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from novelizer.canon.events import EventType

logger = logging.getLogger(__name__)

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

    def __init__(self, events, read_store, kg_store, embedding_store, extraction_runner, cursor_path: str) -> None:
        self._events = events
        self._read = read_store
        self._kg = kg_store
        self._emb = embedding_store
        self._runner = extraction_runner
        self._cursor_path = Path(cursor_path)

    def _load_cursor(self) -> int:
        try:
            return json.loads(self._cursor_path.read_text())["last_sequence"]
        except (OSError, ValueError, KeyError):
            return 0

    def _save_cursor(self, seq: int) -> None:
        tmp_path = self._cursor_path.with_suffix(self._cursor_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps({"last_sequence": seq}))
        os.replace(tmp_path, self._cursor_path)

    async def catch_up(self) -> int:
        processed = 0
        try:
            last = self._load_cursor()
            stored = await self._events.events_since(last, event_types=list(INDEXED_EVENT_TYPES))
            for ev in stored:
                try:
                    await self._index_one(ev.event_type, ev.aggregate_id, ev.sequence)
                except Exception as e:
                    logger.warning("kg indexing stopped at seq %s (%s: %s); will retry",
                                    ev.sequence, type(e).__name__, e)
                    break
                self._save_cursor(ev.sequence)
                processed += 1
        except Exception as e:
            logger.warning("kg indexing catch_up failed (%s: %s); will retry next tick",
                            type(e).__name__, e)
        return processed

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
        await self._commit_facts(entities, relations, fingerprint=f"character:{character_id}", seq=seq)

    async def _index_world_entry(self, entry_id: str, seq: int) -> None:
        from novelizer.store.kg_structured import facts_from_world_entry
        entries = {e.id: e for e in await self._read.list_world_entries()}
        entry = entries.get(entry_id)
        if entry is None:
            return
        entities, relations = facts_from_world_entry(entry)
        await self._commit_facts(entities, relations, fingerprint=f"world_entry:{entry_id}", seq=seq)

    async def _index_chapter(self, chapter_id: str, seq: int) -> None:
        chapter = await self._read.get_chapter(chapter_id)
        if chapter is None:
            return
        fingerprint = f"chapter:{chapter_id}"
        # Reflow: clear this chapter's prior prose-extracted mentions before
        # re-extracting, so a revision that drops a detail also drops its
        # entity's searchability (see class docstring on orphan rows).
        cleared_entity_ids = await self._kg.clear_mentions_for_fingerprint(fingerprint)
        for entity_id in cleared_entity_ids:
            remaining = await self._kg.entity_relations(entity_id)
            still_mentioned = await self._entity_has_other_mentions(entity_id)
            if not remaining and not still_mentioned:
                await self._emb.delete_entity(str(entity_id))

        from novelizer.agents.kg_extraction import kg_extraction_prompt
        prompt = kg_extraction_prompt(chapter.title, chapter.prose)
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": prompt}]})
        out = result.get("structured_response")
        if out is None:
            return

        name_to_id: dict[str, int] = {}
        for entity in out.entities:
            entity_id = await self._kg.upsert_entity(
                entity.name, entity.entity_type, entity.description, seq=seq,
            )
            name_to_id[entity.name] = entity_id
            await self._kg.link_mention(entity_id, fingerprint)
            detail = self._format_entity_detail(entity.name, entity.entity_type, entity.description, [])
            await self._emb.upsert_entity(str(entity_id), entity.name, detail)

        for relation in out.relations:
            source_id = name_to_id.get(relation.source)
            target_id = name_to_id.get(relation.target)
            if source_id is None or target_id is None:
                continue
            await self._kg.upsert_relation(source_id, target_id, relation.relation_type, seq=seq)

    async def _entity_has_other_mentions(self, entity_id: int) -> bool:
        # Structured-source entities (character/world_entry) are re-linked
        # under a stable fingerprint ("character:<id>") every time they're
        # indexed, independent of any chapter fingerprint -- so a chapter
        # reflow never deletes an entity that also has a structured source.
        entity = await self._kg.get_entity(entity_id)
        if entity and entity.get("canon_id"):
            return True
        return False

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
            await self._kg.upsert_relation(source_id, target["id"], rel.relation_type, seq=seq)
