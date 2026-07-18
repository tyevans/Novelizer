from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
import aiosqlite
from pydantic import BaseModel
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

_COLS = "sequence, id, event_type, aggregate_id, payload, created_at"


def _row_to_event(row) -> StoredEvent:
    return StoredEvent(
        sequence=row[0], id=row[1], event_type=row[2],
        aggregate_id=row[3], payload=json.loads(row[4]), created_at=row[5],
    )


class EventStore:
    def __init__(self, path: str) -> None:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_CREATE)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def append(self, event_type: str, aggregate_id: str, payload: BaseModel) -> StoredEvent:
        eid = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        payload_json = payload.model_dump_json()
        cur = await self._conn.execute(
            "INSERT INTO events (id, event_type, aggregate_id, payload, created_at) VALUES (?,?,?,?,?)",
            (eid, event_type, aggregate_id, payload_json, created_at),
        )
        await self._conn.commit()
        return StoredEvent(
            sequence=cur.lastrowid, id=eid, event_type=event_type,
            aggregate_id=aggregate_id, payload=json.loads(payload_json), created_at=created_at,
        )

    async def append_raw(self, event_type: str, aggregate_id: str, payload: dict) -> StoredEvent:
        """Append a payload that is already a plain dict (e.g. rescued from a Proposal)."""
        eid = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload)
        cur = await self._conn.execute(
            "INSERT INTO events (id, event_type, aggregate_id, payload, created_at) VALUES (?,?,?,?,?)",
            (eid, event_type, aggregate_id, payload_json, created_at),
        )
        await self._conn.commit()
        return StoredEvent(
            sequence=cur.lastrowid, id=eid, event_type=event_type,
            aggregate_id=aggregate_id, payload=json.loads(payload_json), created_at=created_at,
        )

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
