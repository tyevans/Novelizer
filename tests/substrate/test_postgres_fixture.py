import asyncpg
import pytest

from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_postgres_dsn_is_a_live_connectable_postgres(postgres_dsn):
    conn = await asyncpg.connect(postgres_dsn)
    try:
        version = await conn.fetchval("SELECT version()")
        assert "PostgreSQL" in version
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_pgvector_extension_is_available(postgres_dsn):
    conn = await asyncpg.connect(postgres_dsn)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        row = await conn.fetchrow("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert row is not None
    finally:
        await conn.close()
