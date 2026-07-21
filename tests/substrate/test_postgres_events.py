import pytest

from substrate.postgres.events import PostgresEventStore
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_append_assigns_monotonic_seq_within_and_across_streams(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        seq1 = await store.append("stream-a", "thing.created", {"n": 1})
        seq2 = await store.append("stream-a", "thing.created", {"n": 2})
        seq3 = await store.append("stream-b", "thing.created", {"n": 3})
        assert seq1 < seq2 < seq3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_read_stream_returns_only_that_streams_events_in_order(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        await store.append("stream-x", "a.created", {"v": 1})
        await store.append("stream-y", "a.created", {"v": 99})
        await store.append("stream-x", "a.updated", {"v": 2})
        rows = await store.read_stream("stream-x")
        assert [r["event_type"] for r in rows] == ["a.created", "a.updated"]
        assert [r["payload"] for r in rows] == [{"v": 1}, {"v": 2}]
        assert rows[0]["seq"] < rows[1]["seq"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_parent_ids_and_actor_round_trip(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        parent = str(__import__("uuid").uuid4())
        await store.append(
            "stream-z", "b.created", {"v": 1}, parent_ids=[parent], actor="scout",
        )
        rows = await store.read_stream("stream-z")
        assert rows[0]["parent_ids"] == [parent]
        assert rows[0]["actor"] == "scout"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_events_table_rejects_update_and_delete(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        await store.append("stream-w", "c.created", {"v": 1})
        with pytest.raises(Exception):
            await store._conn.execute("UPDATE substrate_events SET payload = '{}' WHERE seq = 1")
        with pytest.raises(Exception):
            await store._conn.execute("DELETE FROM substrate_events WHERE seq = 1")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_appends_to_same_stream_preserve_total_order(postgres_dsn):
    import asyncio

    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        async def _append_many(n_start):
            s = PostgresEventStore(postgres_dsn)
            await s.connect()
            try:
                for i in range(5):
                    await s.append("stream-concurrent", "x.created", {"writer": n_start, "i": i})
            finally:
                await s.close()

        await asyncio.gather(*[_append_many(w) for w in range(4)])
        rows = await store.read_stream("stream-concurrent")
        assert len(rows) == 20
        seqs = [r["seq"] for r in rows]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 20  # no lost or duplicate writes
    finally:
        await store.close()
