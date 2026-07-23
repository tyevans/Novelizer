import pytest

from substrate.postgres.deps import PostgresDepsStore
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_blast_radius_of_leaf_is_empty(postgres_dsn):
    store = PostgresDepsStore(postgres_dsn)
    await store.connect()
    try:
        await store.declare_edge("a", "b")
        assert await store.blast_radius("b") == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_blast_radius_follows_multi_hop_chain(postgres_dsn):
    store = PostgresDepsStore(postgres_dsn)
    await store.connect()
    try:
        await store.declare_edge("a", "b")
        await store.declare_edge("b", "c")
        await store.declare_edge("c", "d")
        assert set(await store.blast_radius("a")) == {"b", "c", "d"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_blast_radius_dedupes_diamond_dependency(postgres_dsn):
    store = PostgresDepsStore(postgres_dsn)
    await store.connect()
    try:
        await store.declare_edge("a", "b")
        await store.declare_edge("a", "c")
        await store.declare_edge("b", "d")
        await store.declare_edge("c", "d")
        result = await store.blast_radius("a")
        assert sorted(result) == ["b", "c", "d"]
        assert len(result) == 3  # "d" reachable via two paths, counted once
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_blast_radius_with_cyclic_dependency_terminates_and_dedupes(postgres_dsn):
    store = PostgresDepsStore(postgres_dsn)
    await store.connect()
    try:
        # a -> b -> c -> a is a cycle; the recursive CTE uses UNION (not
        # UNION ALL) so it must dedupe already-visited rows and terminate
        # instead of looping forever.
        await store.declare_edge("a", "b")
        await store.declare_edge("b", "c")
        await store.declare_edge("c", "a")
        result = await store.blast_radius("a")
        assert sorted(result) == ["a", "b", "c"]
        assert len(result) == 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_declare_edge_is_idempotent(postgres_dsn):
    store = PostgresDepsStore(postgres_dsn)
    await store.connect()
    try:
        await store.declare_edge("a", "b")
        await store.declare_edge("a", "b")
        assert await store.blast_radius("a") == ["b"]
    finally:
        await store.close()
