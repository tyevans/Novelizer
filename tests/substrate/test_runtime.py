# tests/substrate/test_runtime.py
import pytest

from substrate import PostgresEventStore, ProjectionCatalog, ProjectionSpec
from substrate.runtime import RuntimeBase
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_catch_up_dispatches_only_to_catalogs_registered_for_the_event_type(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    runtime = RuntimeBase(store, "runtime-test-stream")
    await runtime.connect()
    try:
        seen_a: dict[str, int] = {}
        catalog_a = ProjectionCatalog()
        catalog_a.register(
            ProjectionSpec(
                name="a",
                invalidation_key=lambda event: event.payload["id"],
                recompute=lambda key: seen_a.get(key, 0) + 1,
            )
        )
        seen_b: dict[str, int] = {}
        catalog_b = ProjectionCatalog()
        catalog_b.register(
            ProjectionSpec(
                name="b",
                invalidation_key=lambda event: event.payload["id"],
                recompute=lambda key: seen_b.get(key, 0) + 1,
            )
        )

        runtime.register_projection(catalog_a, "a", {"type.a"})
        runtime.register_projection(catalog_b, "b", {"type.b"})

        await runtime.append("type.a", {"id": "x"})
        await runtime.append("type.b", {"id": "y"})

        await runtime.catch_up()

        assert runtime.get_projection("a") == {"x": 1}
        assert runtime.get_projection("b") == {"y": 1}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_get_projection_returns_empty_dict_before_any_catch_up(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    runtime = RuntimeBase(store, "runtime-test-stream-2")
    await runtime.connect()
    try:
        assert runtime.get_projection("nonexistent") == {}
    finally:
        await runtime.close()
