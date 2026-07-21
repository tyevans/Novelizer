from __future__ import annotations
from typing import Optional
import aiosqlite
from novelizer.canon import db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    canon_id TEXT,
    first_seen INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER NOT NULL DEFAULT 0,
    UNIQUE(name, entity_type)
);

CREATE TABLE IF NOT EXISTS kg_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES kg_entities(id),
    target_id INTEGER NOT NULL REFERENCES kg_entities(id),
    relation_type TEXT NOT NULL,
    first_seen INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, target_id, relation_type)
);

CREATE TABLE IF NOT EXISTS kg_entity_mentions (
    entity_id INTEGER NOT NULL REFERENCES kg_entities(id),
    event_fingerprint TEXT NOT NULL,
    PRIMARY KEY(entity_id, event_fingerprint)
);
"""


class KGStore:
    """Owns the knowledge-graph tables in world.db. A separate connection to
    the same file as ReadStore/EventStore/Projector -- multiple connections
    to one SQLite file, serialized by WAL + busy_timeout, is already the
    norm in this codebase (see novelizer/canon/db.py)."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._conn = await db.connect(self._path)
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def upsert_entity(
        self, name: str, entity_type: str, description: str = "",
        canon_id: str | None = None, seq: int = 0,
    ) -> int:
        existing = await self.find_entity_by_name(name, entity_type)
        if existing:
            await self._conn.execute(
                "UPDATE kg_entities SET description=?, canon_id=COALESCE(?, canon_id), "
                "last_seen=? WHERE id=?",
                (description, canon_id, seq, existing["id"]),
            )
            await self._conn.commit()
            return existing["id"]
        cur = await self._conn.execute(
            "INSERT INTO kg_entities (name, entity_type, description, canon_id, "
            "first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (name, entity_type, description, canon_id, seq, seq),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def upsert_relation(
        self, source_id: int, target_id: int, relation_type: str, seq: int = 0,
    ) -> int:
        cur = await self._conn.execute(
            "SELECT id FROM kg_relations WHERE source_id=? AND target_id=? AND relation_type=?",
            (source_id, target_id, relation_type),
        )
        row = await cur.fetchone()
        if row:
            await self._conn.execute(
                "UPDATE kg_relations SET last_seen=? WHERE id=?", (seq, row[0])
            )
            await self._conn.commit()
            return row[0]
        cur = await self._conn.execute(
            "INSERT INTO kg_relations (source_id, target_id, relation_type, "
            "first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
            (source_id, target_id, relation_type, seq, seq),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def link_mention(self, entity_id: int, event_fingerprint: str) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO kg_entity_mentions (entity_id, event_fingerprint) "
            "VALUES (?, ?)",
            (entity_id, event_fingerprint),
        )
        await self._conn.commit()

    async def clear_mentions_for_fingerprint(self, event_fingerprint: str) -> list[int]:
        cur = await self._conn.execute(
            "SELECT entity_id FROM kg_entity_mentions WHERE event_fingerprint=?",
            (event_fingerprint,),
        )
        ids = [r[0] for r in await cur.fetchall()]
        await self._conn.execute(
            "DELETE FROM kg_entity_mentions WHERE event_fingerprint=?", (event_fingerprint,)
        )
        await self._conn.commit()
        return ids

    async def find_entity_by_name(self, name: str, entity_type: str) -> Optional[dict]:
        cur = await self._conn.execute(
            "SELECT * FROM kg_entities WHERE LOWER(name)=LOWER(?) AND entity_type=?",
            (name, entity_type),
        )
        row = await cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None

    async def get_entity(self, entity_id: int) -> Optional[dict]:
        cur = await self._conn.execute("SELECT * FROM kg_entities WHERE id=?", (entity_id,))
        row = await cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None

    async def entity_relations(self, entity_id: int) -> list[dict]:
        cur = await self._conn.execute(
            "SELECT r.relation_type, e.name as other_name, 'out' as direction "
            "FROM kg_relations r JOIN kg_entities e ON e.id = r.target_id "
            "WHERE r.source_id=? "
            "UNION ALL "
            "SELECT r.relation_type, e.name as other_name, 'in' as direction "
            "FROM kg_relations r JOIN kg_entities e ON e.id = r.source_id "
            "WHERE r.target_id=?",
            (entity_id, entity_id),
        )
        rows = await cur.fetchall()
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in rows]
