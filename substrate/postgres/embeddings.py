from __future__ import annotations
from typing import Optional

import asyncpg


class PostgresEmbeddingStore:
    def __init__(self, dsn: str, dimensions: int) -> None:
        self._dsn = dsn
        self._dimensions = dimensions
        self._conn: Optional[asyncpg.Connection] = None

    async def connect(self) -> None:
        self._conn = await asyncpg.connect(self._dsn)
        # Serialize schema/extension/index creation across concurrently-connecting
        # instances with a Postgres advisory lock, mirroring PostgresEventStore's
        # guard: CREATE EXTENSION / CREATE TABLE / CREATE INDEX can race under
        # concurrent connects.
        async with self._conn.transaction():
            await self._conn.execute("SELECT pg_advisory_xact_lock(727272)")
            await self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await self._conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS substrate_embeddings (
                    target_kind TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    embedding VECTOR({self._dimensions}) NOT NULL,
                    PRIMARY KEY (target_kind, target_id, model)
                )
                """
            )
            await self._conn.execute(
                "CREATE INDEX IF NOT EXISTS substrate_embeddings_hnsw_idx "
                "ON substrate_embeddings USING hnsw (embedding vector_l2_ops)"
            )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    async def upsert(self, target_kind: str, target_id: str, model: str, vector: list[float]) -> None:
        vector_literal = "[" + ",".join(str(v) for v in vector) + "]"
        await self._conn.execute(
            "INSERT INTO substrate_embeddings (target_kind, target_id, model, embedding) "
            "VALUES ($1, $2, $3, $4::vector) "
            "ON CONFLICT (target_kind, target_id, model) DO UPDATE SET embedding = EXCLUDED.embedding",
            target_kind, target_id, model, vector_literal,
        )

    async def nearest(self, model: str, query_vector: list[float], limit: int = 5) -> list[dict]:
        vector_literal = "[" + ",".join(str(v) for v in query_vector) + "]"
        rows = await self._conn.fetch(
            "SELECT target_kind, target_id, embedding <-> $1::vector AS distance "
            "FROM substrate_embeddings WHERE model = $2 "
            "ORDER BY embedding <-> $1::vector ASC LIMIT $3",
            vector_literal, model, limit,
        )
        return [
            {"target_kind": r["target_kind"], "target_id": r["target_id"], "distance": r["distance"]}
            for r in rows
        ]
