from __future__ import annotations
import json
from typing import Any, Optional

import asyncpg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS substrate_events (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stream_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    parent_ids UUID[] NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS substrate_events_stream_id_seq_idx
    ON substrate_events (stream_id, seq);

CREATE OR REPLACE FUNCTION substrate_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'substrate_events is append-only: % not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS substrate_events_no_update ON substrate_events;
CREATE TRIGGER substrate_events_no_update
    BEFORE UPDATE ON substrate_events
    FOR EACH ROW EXECUTE FUNCTION substrate_events_append_only();

DROP TRIGGER IF EXISTS substrate_events_no_delete ON substrate_events;
CREATE TRIGGER substrate_events_no_delete
    BEFORE DELETE ON substrate_events
    FOR EACH ROW EXECUTE FUNCTION substrate_events_append_only();
"""


class PostgresEventStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: Optional[asyncpg.Connection] = None

    async def connect(self) -> None:
        self._conn = await asyncpg.connect(self._dsn)
        # Serialize schema creation across concurrently-connecting instances
        # with a Postgres advisory lock: concurrent DDL (CREATE OR REPLACE
        # FUNCTION / DROP+CREATE TRIGGER) on the same objects can deadlock.
        async with self._conn.transaction():
            await self._conn.execute("SELECT pg_advisory_xact_lock(727271)")
            await self._conn.execute(_SCHEMA)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    async def append(
        self,
        stream_id: str,
        event_type: str,
        payload: dict,
        parent_ids: list[str] | None = None,
        actor: str = "",
    ) -> int:
        row = await self._conn.fetchrow(
            "INSERT INTO substrate_events (stream_id, event_type, payload, parent_ids, actor) "
            "VALUES ($1, $2, $3::jsonb, $4::uuid[], $5) RETURNING seq",
            stream_id, event_type, json.dumps(payload), parent_ids or [], actor,
        )
        return row["seq"]

    async def read_stream(self, stream_id: str) -> list[dict]:
        rows = await self._conn.fetch(
            "SELECT seq, stream_id, event_type, payload, parent_ids, actor, created_at "
            "FROM substrate_events WHERE stream_id = $1 ORDER BY seq ASC",
            stream_id,
        )
        return [
            {
                "seq": r["seq"],
                "stream_id": r["stream_id"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload"]),
                "parent_ids": [str(p) for p in r["parent_ids"]],
                "actor": r["actor"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
