from __future__ import annotations

import json
import logging
import os
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
    EventType.PROMISE_MADE, EventType.PROMISE_PROGRESSED,
    EventType.PROMISE_PAID, EventType.PROMISE_RELEASED,
    EventType.CHAPTER_BRIEF_DRAFTED,
    EventType.CHAPTER_BRIEF_SUPERSEDED, EventType.CHAPTER_BRIEF_FULFILLED,
    EventType.ARC_DECLARED, EventType.ARC_ADVANCED, EventType.ARC_RESOLVED,
]

_PREFIX_TO_KIND = {
    "chapter": "chapter",
    "world_entry": "world",
    "character": "character",
    "thread": "thread",
    "secret": "secret",
    "theme": "theme",
    "promise": "promise",
    "chapter_brief": "brief",
    "arc": "arc",
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
        # Atomic write: tmp file + os.replace, so a crash mid-write never
        # leaves a truncated/corrupt cursor file behind.
        tmp_path = self._cursor_path.with_suffix(self._cursor_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps({"last_sequence": seq}))
        os.replace(tmp_path, self._cursor_path)

    async def catch_up(self) -> int:
        processed = 0
        try:
            last = self._load_cursor()
            stored = await self._events.events_since(
                last, event_types=list(INDEXED_EVENT_TYPES)
            )
            for ev in stored:
                try:
                    await self._index_one(ev.event_type, ev.aggregate_id)
                except Exception as e:  # endpoint down, malformed record, ...
                    logger.warning("canon indexing stopped at seq %s (%s: %s); will retry",
                                   ev.sequence, type(e).__name__, e)
                    break
                self._save_cursor(ev.sequence)
                processed += 1
        except Exception as e:
            # events_since (e.g. "database is locked") or _save_cursor
            # (OSError) escaping here would violate the never-raise contract
            # Runtime.start() relies on -- log and return what was processed.
            logger.warning("canon indexing catch_up failed (%s: %s); will retry next tick",
                            type(e).__name__, e)
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
                supersedes_id = getattr(record, "supersedes_id", None)
                if supersedes_id:
                    # Real retconner convention: WORLD_ENTRY_SUPERSEDED is
                    # committed with aggregate_id = the NEW entry (found
                    # active above), while supersedes_id names the RETIRING
                    # entry. That entry stays active-list-absent-blind (it
                    # was never itself the aggregate_id of this event), so
                    # its stale embedding must be deleted here explicitly.
                    try:
                        await self._emb.delete(supersedes_id, "world_entries")
                    except Exception as e:
                        logger.debug("superseded world entry %s not present in index to delete (%s: %s)",
                                     supersedes_id, type(e).__name__, e)
            else:  # superseded out of the active list
                try:
                    await self._emb.delete(aggregate_id, "world_entries")
                except Exception as e:
                    logger.debug("world entry %s not present in index to delete (%s: %s)",
                                 aggregate_id, type(e).__name__, e)
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
        elif kind == "theme":
            record = await self._read.get_theme(aggregate_id)
            if record is not None:
                await self._emb.upsert_theme(record)
        elif kind == "promise":
            record = await self._read.get_promise(aggregate_id)
            if record is not None:
                await self._emb.upsert_promise(record)
        elif kind == "brief":
            briefs = {b.id: b for b in await self._read.list_briefs()}
            record = briefs.get(aggregate_id)
            if record is not None:
                await self._emb.upsert_brief(record)
        else:
            record = await self._read.get_arc(aggregate_id)
            if record is not None:
                await self._emb.upsert_arc(record)
