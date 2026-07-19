from __future__ import annotations

import json
import logging
from pathlib import Path

from novelizer.canon.events import EventType

logger = logging.getLogger(__name__)

# Every event type that changes what a canon record should embed as.
INDEXED_EVENT_TYPES = [
    EventType.CHAPTER_CREATED, EventType.CHAPTER_REVISED,
    EventType.WORLD_ENTRY_CREATED, EventType.WORLD_ENTRY_SUPERSEDED,
    EventType.CHARACTER_CREATED, EventType.CHARACTER_UPDATED,
    EventType.THREAD_PLANTED, EventType.THREAD_TOUCHED,
    EventType.THREAD_PAID_OFF, EventType.THREAD_ABANDONED,
    EventType.SECRET_CREATED, EventType.SECRET_REVEALED,
    EventType.THEME_INTRODUCED, EventType.THEME_DEVELOPED,
]

_PREFIX_TO_KIND = {
    "chapter": "chapter",
    "world_entry": "world",
    "character": "character",
    "thread": "thread",
    "secret": "secret",
    "theme": "theme",
}


class CanonIndexer:
    """Event-cursor-driven embedding indexer (Projector's cursor pattern,
    but hydrating CURRENT records from ReadStore so create/revise/update
    share one path). Failure-tolerant by contract: an embed-endpoint outage
    logs a warning and leaves the cursor at the last indexed event, so the
    next catch_up retries. Never writes to world.db.
    """

    def __init__(self, events, read_store, embedding_store, cursor_path: str) -> None:
        self._events = events
        self._read = read_store
        self._emb = embedding_store
        self._cursor_path = Path(cursor_path)

    def _load_cursor(self) -> int:
        try:
            return json.loads(self._cursor_path.read_text())["last_sequence"]
        except (OSError, ValueError, KeyError):
            return 0

    def _save_cursor(self, seq: int) -> None:
        self._cursor_path.write_text(json.dumps({"last_sequence": seq}))

    async def catch_up(self) -> int:
        last = self._load_cursor()
        stored = await self._events.events_since(
            last, event_types=list(INDEXED_EVENT_TYPES)
        )
        processed = 0
        for ev in stored:
            try:
                await self._index_one(ev.event_type, ev.aggregate_id)
            except Exception as e:  # endpoint down, malformed record, ...
                logger.warning("canon indexing stopped at seq %s (%s: %s); will retry",
                               ev.sequence, type(e).__name__, e)
                break
            self._save_cursor(ev.sequence)
            processed += 1
        return processed

    async def _index_one(self, event_type: str, aggregate_id: str) -> None:
        kind = _PREFIX_TO_KIND[event_type.split(".")[0]]
        if kind == "chapter":
            record = await self._read.get_chapter(aggregate_id)
            if record is not None:
                await self._emb.upsert_chapter(record)
        elif kind == "world":
            entries = {e.id: e for e in await self._read.list_world_entries()}
            record = entries.get(aggregate_id)
            if record is not None:
                await self._emb.upsert_world_entry(record)
            else:  # superseded out of the active list
                try:
                    await self._emb.delete(aggregate_id, "world_entries")
                except Exception:
                    pass
        elif kind == "character":
            record = await self._read.get_character(aggregate_id)
            if record is not None:
                await self._emb.upsert_character(record)
        elif kind == "thread":
            record = await self._read.get_thread(aggregate_id)
            if record is not None:
                await self._emb.upsert_thread(record)
        elif kind == "secret":
            record = await self._read.get_secret(aggregate_id)
            if record is not None:
                await self._emb.upsert_secret(record)
        else:
            record = await self._read.get_theme(aggregate_id)
            if record is not None:
                await self._emb.upsert_theme(record)
