from __future__ import annotations
from typing import Optional

import asyncpg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS substrate_derived_deps (
    parent TEXT NOT NULL,
    child TEXT NOT NULL,
    PRIMARY KEY (parent, child)
);
"""

_BLAST_RADIUS_QUERY = """
WITH RECURSIVE descendants AS (
    SELECT child FROM substrate_derived_deps WHERE parent = $1
    UNION
    SELECT d.child
    FROM substrate_derived_deps d
    JOIN descendants dsc ON d.parent = dsc.child
)
SELECT child FROM descendants
"""


class PostgresDepsStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: Optional[asyncpg.Connection] = None

    async def connect(self) -> None:
        self._conn = await asyncpg.connect(self._dsn)
        # Serialize schema creation across concurrently-connecting instances with
        # a Postgres advisory lock, mirroring PostgresEventStore (727271) and
        # PostgresEmbeddingStore (727272): CREATE TABLE can race under concurrent
        # connects.
        async with self._conn.transaction():
            await self._conn.execute("SELECT pg_advisory_xact_lock(727273)")
            await self._conn.execute(_SCHEMA)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    async def declare_edge(self, parent: str, child: str) -> None:
        await self._conn.execute(
            "INSERT INTO substrate_derived_deps (parent, child) VALUES ($1, $2) "
            "ON CONFLICT (parent, child) DO NOTHING",
            parent, child,
        )

    async def blast_radius(self, node: str) -> list[str]:
        rows = await self._conn.fetch(_BLAST_RADIUS_QUERY, node)
        return [r["child"] for r in rows]
