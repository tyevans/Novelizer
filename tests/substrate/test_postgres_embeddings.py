import pytest

from substrate.postgres.embeddings import PostgresEmbeddingStore
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_upsert_then_nearest_finds_closest_vector(postgres_dsn):
    store = PostgresEmbeddingStore(postgres_dsn, dimensions=3)
    await store.connect()
    try:
        await store.upsert("chapter", "ch-1", "model-a", [1.0, 0.0, 0.0])
        await store.upsert("chapter", "ch-2", "model-a", [0.0, 1.0, 0.0])
        results = await store.nearest("model-a", [0.9, 0.1, 0.0], limit=1)
        assert results[0]["target_kind"] == "chapter"
        assert results[0]["target_id"] == "ch-1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_upsert_same_target_and_model_replaces_not_duplicates(postgres_dsn):
    store = PostgresEmbeddingStore(postgres_dsn, dimensions=3)
    await store.connect()
    try:
        await store.upsert("chapter", "ch-1", "model-a", [1.0, 0.0, 0.0])
        await store.upsert("chapter", "ch-1", "model-a", [0.0, 0.0, 1.0])
        results = await store.nearest("model-a", [0.0, 0.0, 0.9], limit=5)
        matches = [r for r in results if r["target_id"] == "ch-1"]
        assert len(matches) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_nearest_filters_by_model(postgres_dsn):
    store = PostgresEmbeddingStore(postgres_dsn, dimensions=3)
    await store.connect()
    try:
        await store.upsert("chapter", "ch-1", "model-a", [1.0, 0.0, 0.0])
        await store.upsert("chapter", "ch-2", "model-b", [1.0, 0.0, 0.0])
        results = await store.nearest("model-b", [1.0, 0.0, 0.0], limit=5)
        assert [r["target_id"] for r in results] == ["ch-2"]
    finally:
        await store.close()
