import os
import tempfile
import pytest
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter, WorldEntry


@pytest.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = EventStore(path)
    await s.init()
    yield s
    await s.close()
    os.unlink(path)


async def test_append_returns_monotonic_sequence(store):
    e1 = await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
    e2 = await store.append(EventType.CHAPTER_CREATED, "c2", Chapter(title="B", prose="b"))
    assert e1.sequence == 1 and e2.sequence == 2
    assert e1.payload["title"] == "A"


async def test_events_since_excludes_at_or_below(store):
    await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
    e2 = await store.append(EventType.CHAPTER_CREATED, "c2", Chapter(title="B", prose="b"))
    tail = await store.events_since(1)
    assert [e.sequence for e in tail] == [2]
    assert tail[0].id == e2.id


async def test_events_since_type_filter(store):
    await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
    await store.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(title="W", body="w"))
    only = await store.events_since(0, event_types=[EventType.WORLD_ENTRY_CREATED])
    assert [e.event_type for e in only] == [EventType.WORLD_ENTRY_CREATED]


async def test_count_since_excludes_at_or_below(store):
    await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
    await store.append(EventType.CHAPTER_CREATED, "c2", Chapter(title="B", prose="b"))
    assert await store.count_since(0) == 2
    assert await store.count_since(1) == 1
    assert await store.count_since(2) == 0


async def test_count_since_type_filter(store):
    await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
    await store.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(title="W", body="w"))
    assert await store.count_since(0, event_types=[EventType.WORLD_ENTRY_CREATED]) == 1
    assert await store.count_since(0, event_types=[EventType.THREAD_PLANTED]) == 0
    # A falsy filter means "every type", exactly as events_since reads it. The
    # lag() callers build this list from a module constant, so an empty one
    # must not silently flip the meaning to "count nothing".
    assert await store.count_since(0, event_types=None) == 2
    assert await store.count_since(0, event_types=[]) == 2


_COUNTABLE_TYPES = [
    EventType.CHAPTER_CREATED, EventType.WORLD_ENTRY_CREATED, EventType.THREAD_PLANTED,
]


@settings(deadline=None, max_examples=25)
@given(
    kinds=st.lists(st.sampled_from(_COUNTABLE_TYPES), max_size=12),
    cursor=st.integers(min_value=0, max_value=13),
    filter_size=st.integers(min_value=0, max_value=len(_COUNTABLE_TYPES)),
)
async def test_count_since_always_agrees_with_events_since(kinds, cursor, filter_size):
    """count_since exists only to avoid hydrating rows lag() throws away, so
    its answer must be indistinguishable from len(events_since(...)) for every
    cursor and every filter -- including the empty one."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = EventStore(path)
    await s.init()
    try:
        for i, kind in enumerate(kinds):
            await s.append_raw(kind, f"a{i}", {"i": i})
        event_types = _COUNTABLE_TYPES[:filter_size]
        assert await s.count_since(cursor, event_types=event_types) == len(
            await s.events_since(cursor, event_types=event_types)
        )
    finally:
        await s.close()
        os.unlink(path)


@settings(deadline=None)  # SQLite I/O under load trips the 200ms default; wall-clock is not the invariant here
@given(n=st.integers(min_value=1, max_value=25))
async def test_sequences_are_strictly_increasing(n):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = EventStore(path)
    await s.init()
    try:
        seqs = [
            (await s.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(title=str(i), prose="x"))).sequence
            for i in range(n)
        ]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == n
    finally:
        await s.close()
        os.unlink(path)


async def test_append_raw_stores_dict_payload_without_a_model():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = EventStore(path)
    await store.init()
    try:
        stored = await store.append_raw(EventType.CHAPTER_CREATED, "c1", {"id": "c1", "title": "One", "prose": "p"})
        assert stored.event_type == EventType.CHAPTER_CREATED
        assert stored.payload["title"] == "One"
        fetched = await store.events_since(0)
        assert fetched[0].payload["title"] == "One"
    finally:
        await store.close()
        os.unlink(path)


async def test_append_stores_and_returns_run_id(store):
    ev = await store.append(EventType.CHAPTER_CREATED, "c1",
                            Chapter(title="A", prose="a"), run_id="run-42")
    assert ev.run_id == "run-42"
    fetched = await store.events_since(0)
    assert fetched[0].run_id == "run-42"


async def test_append_without_run_id_defaults_to_none(store):
    ev = await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
    assert ev.run_id is None
    assert (await store.events_since(0))[0].run_id is None


async def test_events_for_run_returns_only_that_runs_events_in_order(store):
    await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"), run_id="r1")
    await store.append(EventType.CHAPTER_CREATED, "c2", Chapter(title="B", prose="b"), run_id="r2")
    await store.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(title="W", body="w"), run_id="r1")
    got = await store.events_for_run("r1")
    assert [e.aggregate_id for e in got] == ["c1", "w1"]


async def test_events_tail_returns_last_n_in_ascending_order(store):
    for i in range(5):
        await store.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(title=str(i), prose="x"))
    tail = await store.events_tail(2)
    assert [e.sequence for e in tail] == [4, 5]
    full = await store.events_tail(10)
    assert [e.sequence for e in full] == [1, 2, 3, 4, 5]


async def test_init_migrates_a_pre_run_id_database():
    import aiosqlite
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # Build a DB with the pre-migration schema (no run_id column) and one row.
    old_schema = """
    CREATE TABLE IF NOT EXISTS events (
        sequence     INTEGER PRIMARY KEY AUTOINCREMENT,
        id           TEXT NOT NULL UNIQUE,
        event_type   TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        payload      TEXT NOT NULL,
        created_at   TEXT NOT NULL
    );
    """
    conn = await aiosqlite.connect(path)
    await conn.executescript(old_schema)
    await conn.execute(
        "INSERT INTO events (id, event_type, aggregate_id, payload, created_at) VALUES (?,?,?,?,?)",
        ("old-1", EventType.CHAPTER_CREATED, "c1", '{"title": "Old", "prose": "p"}', "t"),
    )
    await conn.commit()
    await conn.close()
    s = EventStore(path)
    await s.init()  # must ALTER TABLE, not crash
    try:
        old = await s.events_since(0)
        assert old[0].run_id is None and old[0].payload["title"] == "Old"
        newer = await s.append(EventType.CHAPTER_CREATED, "c2",
                               Chapter(title="New", prose="p"), run_id="r9")
        assert newer.run_id == "r9"
    finally:
        await s.close()
        os.unlink(path)


async def test_events_before_returns_the_window_just_below_a_sequence(tmp_path):
    store = EventStore(str(tmp_path / "t.db"))
    await store.init()
    try:
        for i in range(10):
            await store.append_raw("x.happened", "agg", {"i": i})
        window = await store.events_before(sequence=8, limit=3)
        assert [e.payload["i"] for e in window] == [4, 5, 6]
    finally:
        await store.close()


async def test_events_before_is_ascending_and_clamps_at_the_beginning(tmp_path):
    store = EventStore(str(tmp_path / "t.db"))
    await store.init()
    try:
        for i in range(3):
            await store.append_raw("x.happened", "agg", {"i": i})
        window = await store.events_before(sequence=2, limit=50)
        assert [e.payload["i"] for e in window] == [0]
        assert await store.events_before(sequence=1, limit=50) == []
    finally:
        await store.close()


async def test_events_before_filters_by_type(tmp_path):
    store = EventStore(str(tmp_path / "t.db"))
    await store.init()
    try:
        await store.append_raw("a.happened", "agg", {"i": 0})
        await store.append_raw("b.happened", "agg", {"i": 1})
        await store.append_raw("a.happened", "agg", {"i": 2})
        window = await store.events_before(sequence=99, limit=10, event_types=["a.happened"])
        assert [e.payload["i"] for e in window] == [0, 2]
    finally:
        await store.close()
