from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from novelizer.canon.events import EventType
from novelizer.store.drain import drain_pending
from novelizer.store.poison_ladder import PoisonLadder

logger = logging.getLogger(__name__)

# Every event type that changes what a canon record should embed as.
INDEXED_EVENT_TYPES = [
    EventType.CHAPTER_CREATED, EventType.CHAPTER_REVISED,
    EventType.WORLD_ENTRY_CREATED, EventType.WORLD_ENTRY_SUPERSEDED,
    EventType.WORLD_ENTRY_RETIRED,
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


class ProjectionNotReady(RuntimeError):
    """The record this event should index has not been projected into the read
    store yet, so there is nothing to embed -- YET.

    Raised rather than skipped because the drain reads a clean return as "this
    sequence is done" and advances the cursor over it. A no-op success there is
    permanent, silent loss: the embedding is never written and the event is
    never seen again (observed in production as embed_cursor.json parked at
    sequence 97 of 117 with an index of zero documents and a reported backlog of
    zero). A raise routes the event through the poison ladder instead, which
    retries it and -- crucially -- still gives up after poison_skip_after passes,
    so a genuinely bad event cannot wedge the drain.
    """


def _require_projected(record, kind: str, aggregate_id: str):
    """Guard for a hydrate-current-record read: absence means not-ready, not done."""
    if record is None:
        raise ProjectionNotReady(
            f"{kind} {aggregate_id!r} is not in the read store yet; "
            f"the canon projection is behind the event log"
        )
    return record


class CanonIndexer:
    """Event-cursor-driven embedding indexer (Projector's cursor pattern,
    but hydrating CURRENT records from ReadStore so create/revise/update
    share one path). Failure-tolerant by contract: an embed-endpoint outage
    logs a warning and leaves the cursor at the last indexed event, so the
    next catch_up retries -- up to poison_skip_after consecutive attempts on
    the same event, after which it is abandoned. A record the canon projection
    has not written yet takes that same retry path (ProjectionNotReady) rather
    than counting as indexed. Never writes to world.db.
    """

    def __init__(self, events, read_store, embedding_store, cursor_path: str,
                 poison_skip_after: int = 3, pool=None, drain_concurrency: int = 4) -> None:
        self._events = events
        self._read = read_store
        self._emb = embedding_store
        self._cursor_path = Path(cursor_path)
        self._poison = PoisonLadder(poison_skip_after)
        # Shared LLM/endpoint ceiling (duck-typed AdaptivePool) and the fan-out
        # cap for the parallel drain. None pool => no permit gating, still
        # parallel. See store/drain.py for the drain algorithm both projectors
        # share -- this indexer's embed-only writes vs KGProjector's world.db
        # writes are their one real difference.
        self._pool = pool
        self._drain_concurrency = drain_concurrency

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

    async def lag(self) -> int:
        """Read-only: how many indexable events haven't been embedded yet.
        Reuses _load_cursor and the same filter catch_up applies, but counts
        in SQL -- a 10k-event backlog is a COUNT, not 10k hydrated rows.
        Never mutates the cursor and never calls _index_one."""
        last = self._load_cursor()
        return await self._events.count_since(last, event_types=list(INDEXED_EVENT_TYPES))

    async def catch_up(self) -> int:
        try:
            last = self._load_cursor()
            stored = await self._events.events_since(
                last, event_types=list(INDEXED_EVENT_TYPES)
            )
            # Parallel drain (Phase 5): under the strict background gate the
            # drain is the room's critical path, so it partitions the window by
            # aggregate and drains partitions concurrently rather than looping.
            # Embed-only writes to distinct aggregate ids need no projector-level
            # commit lock -- EmbeddingStore already serializes its own writes
            # (store/embeddings.py _write_lock) -- so this drain is fully
            # parallel, unlike KGProjector's which serializes its world.db
            # read-modify-writes.
            return await drain_pending(
                stored,
                poison=self._poison,
                pool=self._pool,
                drain_concurrency=self._drain_concurrency,
                run_one=lambda ev: self._index_one(ev.event_type, ev.aggregate_id),
                save_cursor=self._save_cursor,
                logger=logger,
                label="canon indexing",
            )
        except Exception as e:
            # events_since (e.g. "database is locked") or _save_cursor
            # (OSError) escaping here would violate the never-raise contract
            # Runtime.start() relies on -- log and return nothing processed.
            logger.warning("canon indexing catch_up failed (%s: %s); will retry next tick",
                            type(e).__name__, e)
            return 0

    async def _index_one(self, event_type: str, aggregate_id: str) -> None:
        kind = _PREFIX_TO_KIND[event_type.split(".")[0]]
        if kind == "chapter":
            record = _require_projected(await self._read.get_chapter(aggregate_id),
                                        kind, aggregate_id)
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
            elif await self._read.get_world_entry(aggregate_id) is None:
                # No row at all: absence here is the projection lagging, not a
                # retirement, and list_world_entries cannot tell the two apart
                # (both read as "not in the active list").
                raise ProjectionNotReady(
                    f"world entry {aggregate_id!r} is not in the read store yet; "
                    f"the canon projection is behind the event log"
                )
            else:
                # Row present but no longer active (superseded or retired): it
                # legitimately leaves the search index. Genuinely done.
                try:
                    await self._emb.delete(aggregate_id, "world_entries")
                except Exception as e:
                    logger.debug("world entry %s not present in index to delete (%s: %s)",
                                 aggregate_id, type(e).__name__, e)
        elif kind == "character":
            record = _require_projected(await self._read.get_character(aggregate_id),
                                        kind, aggregate_id)
            await self._emb.upsert_character(record)
        elif kind == "thread":
            record = _require_projected(await self._read.get_thread(aggregate_id),
                                        kind, aggregate_id)
            await self._emb.upsert_thread(record)
        elif kind == "secret":
            record = _require_projected(await self._read.get_secret(aggregate_id),
                                        kind, aggregate_id)
            await self._emb.upsert_secret(record)
        elif kind == "theme":
            record = _require_projected(await self._read.get_theme(aggregate_id),
                                        kind, aggregate_id)
            await self._emb.upsert_theme(record)
        elif kind == "promise":
            record = _require_projected(await self._read.get_promise(aggregate_id),
                                        kind, aggregate_id)
            await self._emb.upsert_promise(record)
        elif kind == "brief":
            briefs = {b.id: b for b in await self._read.list_briefs()}
            record = _require_projected(briefs.get(aggregate_id), kind, aggregate_id)
            await self._emb.upsert_brief(record)
        elif kind == "arc":
            record = _require_projected(await self._read.get_arc(aggregate_id),
                                        kind, aggregate_id)
            await self._emb.upsert_arc(record)
        else:
            logger.warning("canon indexer: unknown kind %r for event_type %s (aggregate %s); skipping",
                            kind, event_type, aggregate_id)
