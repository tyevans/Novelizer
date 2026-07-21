import asyncio
import pytest

from substrate.postgres.events import PostgresEventStore
from substrate.postgres.embeddings import PostgresEmbeddingStore
from substrate.postgres.deps import PostgresDepsStore
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_concurrent_agents_write_events_embeddings_and_deps_without_loss(postgres_dsn):
    async def _agent(agent_id: int):
        events = PostgresEventStore(postgres_dsn)
        embeddings = PostgresEmbeddingStore(postgres_dsn, dimensions=2)
        deps = PostgresDepsStore(postgres_dsn)
        await events.connect()
        await embeddings.connect()
        await deps.connect()
        try:
            for i in range(10):
                target = f"agent-{agent_id}-item-{i}"
                await events.append("shared-stream", "claim.proposed", {"agent": agent_id, "i": i})
                await embeddings.upsert("claim", target, "model-a", [float(agent_id), float(i)])
                if i > 0:
                    await deps.declare_edge(f"agent-{agent_id}-item-{i - 1}", target)
        finally:
            await events.close()
            await embeddings.close()
            await deps.close()

    await asyncio.gather(*[_agent(a) for a in range(6)])

    verify_events = PostgresEventStore(postgres_dsn)
    await verify_events.connect()
    try:
        rows = await verify_events.read_stream("shared-stream")
        assert len(rows) == 60
        seqs = [r["seq"] for r in rows]
        assert len(set(seqs)) == 60
    finally:
        await verify_events.close()

    verify_deps = PostgresDepsStore(postgres_dsn)
    await verify_deps.connect()
    try:
        chain = await verify_deps.blast_radius("agent-0-item-0")
        assert set(chain) == {f"agent-0-item-{i}" for i in range(1, 10)}
    finally:
        await verify_deps.close()
