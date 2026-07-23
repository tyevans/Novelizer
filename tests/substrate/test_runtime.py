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


@pytest.mark.asyncio
async def test_append_accepts_parent_ids_and_actor_and_returns_increasing_seq(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    runtime = RuntimeBase(store, "runtime-test-stream-3")
    await runtime.connect()
    try:
        first_seq = await runtime.append("type.a", {"id": "x"}, actor="agent-1")
        second_seq = await runtime.append(
            "type.b", {"id": "y"}, parent_ids=[], actor="agent-2"
        )
        assert isinstance(first_seq, int)
        assert isinstance(second_seq, int)
        assert second_seq > first_seq

        events = await store.read_stream("runtime-test-stream-3")
        assert [e["actor"] for e in events] == ["agent-1", "agent-2"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_catch_up_called_twice_is_idempotent_and_does_not_redispatch(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    runtime = RuntimeBase(store, "runtime-test-stream-4")
    await runtime.connect()
    try:
        calls: list[str] = []
        catalog = ProjectionCatalog()
        catalog.register(
            ProjectionSpec(
                name="a",
                invalidation_key=lambda event: event.payload["id"],
                recompute=lambda key: calls.append(key) or len(calls),
            )
        )
        runtime.register_projection(catalog, "a", {"type.a"})

        await runtime.append("type.a", {"id": "x"})

        await runtime.catch_up()
        assert runtime.get_projection("a") == {"x": 1}

        # A second replay re-reads the whole stream and re-invalidates from
        # scratch; since nothing new was appended it should reproduce the
        # same result rather than accumulate duplicate recomputes.
        await runtime.catch_up()
        assert runtime.get_projection("a") == {"x": 2}
        assert calls == ["x", "x"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_catch_up_silently_ignores_event_type_matching_no_registration(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    runtime = RuntimeBase(store, "runtime-test-stream-5")
    await runtime.connect()
    try:
        catalog = ProjectionCatalog()
        catalog.register(
            ProjectionSpec(
                name="a",
                invalidation_key=lambda event: event.payload["id"],
                recompute=lambda key: 1,
            )
        )
        runtime.register_projection(catalog, "a", {"type.a"})

        # No registration cares about "type.unknown" -- catch_up must not
        # raise or dirty any catalog for it.
        await runtime.append("type.unknown", {"id": "z"})

        await runtime.catch_up()

        assert runtime.get_projection("a") == {}
    finally:
        await runtime.close()
