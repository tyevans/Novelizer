from __future__ import annotations
import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
import aiosqlite
from pydantic import BaseModel
from novelizer.canon import db
from novelizer.canon.events import StoredEvent

_CREATE = """
CREATE TABLE IF NOT EXISTS events (
    sequence     INTEGER PRIMARY KEY AUTOINCREMENT,
    id           TEXT NOT NULL UNIQUE,
    event_type   TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""

_COLS = "sequence, id, event_type, aggregate_id, payload, created_at, run_id"


def _row_to_event(row) -> StoredEvent:
    return StoredEvent(
        sequence=row[0], id=row[1], event_type=row[2],
        aggregate_id=row[3], payload=json.loads(row[4]), created_at=row[5],
        run_id=row[6],
    )


class EventStore:
    def __init__(self, path: str) -> None:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None
        # Concurrent appends share this connection; the lock keeps one append's
        # INSERT/COMMIT (and any rollback-on-retry) from interleaving with
        # another task's in-flight transaction.
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        self._conn = await db.connect(self._path)
        await self._conn.executescript(_CREATE)
        # Additive migration: pre-telemetry DBs lack run_id; existing rows stay NULL.
        cur = await self._conn.execute("PRAGMA table_info(events)")
        cols = [r[1] for r in await cur.fetchall()]
        if "run_id" not in cols:
            await self._conn.execute("ALTER TABLE events ADD COLUMN run_id TEXT")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def _insert(self, event_type: str, aggregate_id: str, payload_json: str,
                      run_id: Optional[str] = None) -> StoredEvent:
        eid = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        async def txn() -> int:
            if self._conn.in_transaction:
                await self._conn.execute("ROLLBACK")
            cur = await self._conn.execute(
                "INSERT INTO events (id, event_type, aggregate_id, payload, created_at, run_id) VALUES (?,?,?,?,?,?)",
                (eid, event_type, aggregate_id, payload_json, created_at, run_id),
            )
            await self._conn.commit()
            return cur.lastrowid

        async with self._write_lock:
            sequence = await db.retry_locked(txn)
        return StoredEvent(
            sequence=sequence, id=eid, event_type=event_type,
            aggregate_id=aggregate_id, payload=json.loads(payload_json), created_at=created_at,
            run_id=run_id,
        )

    async def append(self, event_type: str, aggregate_id: str, payload: BaseModel,
                     run_id: Optional[str] = None) -> StoredEvent:
        return await self._insert(event_type, aggregate_id, payload.model_dump_json(), run_id)

    async def append_raw(self, event_type: str, aggregate_id: str, payload: dict,
                         run_id: Optional[str] = None) -> StoredEvent:
        """Append a payload that is already a plain dict (e.g. rescued from a Proposal)."""
        return await self._insert(event_type, aggregate_id, json.dumps(payload), run_id)

    async def events_for_run(self, run_id: str) -> list[StoredEvent]:
        cur = await self._conn.execute(
            f"SELECT {_COLS} FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
        )
        return [_row_to_event(r) for r in await cur.fetchall()]

    async def events_tail(self, limit: int) -> list[StoredEvent]:
        """Last `limit` events in ascending sequence order."""
        cur = await self._conn.execute(
            f"SELECT {_COLS} FROM events ORDER BY sequence DESC LIMIT ?", (limit,)
        )
        rows = await cur.fetchall()
        return [_row_to_event(r) for r in reversed(rows)]

    async def events_since(self, sequence: int, event_types: Optional[list[str]] = None) -> list[StoredEvent]:
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            cur = await self._conn.execute(
                f"SELECT {_COLS} FROM events WHERE sequence > ? AND event_type IN ({placeholders}) ORDER BY sequence",
                (sequence, *event_types),
            )
        else:
            cur = await self._conn.execute(
                f"SELECT {_COLS} FROM events WHERE sequence > ? ORDER BY sequence",
                (sequence,),
            )
        rows = await cur.fetchall()
        return [_row_to_event(r) for r in rows]

    async def count_since(self, sequence: int, event_types: Optional[list[str]] = None) -> int:
        """How many events events_since would return, without hydrating them.
        The filter is read with the same truthiness as events_since, so None
        and [] both mean "every type" -- lag() callers build the list from a
        module constant and must not get "count nothing" from an empty one."""
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            cur = await self._conn.execute(
                f"SELECT COUNT(*) FROM events WHERE sequence > ? AND event_type IN ({placeholders})",
                (sequence, *event_types),
            )
        else:
            cur = await self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE sequence > ?",
                (sequence,),
            )
        row = await cur.fetchone()
        return row[0]

    async def events_for_aggregate(self, aggregate_id: str) -> list[StoredEvent]:
        cur = await self._conn.execute(
            f"SELECT {_COLS} FROM events WHERE aggregate_id=? ORDER BY sequence",
            (aggregate_id,),
        )
        return [_row_to_event(r) for r in await cur.fetchall()]
