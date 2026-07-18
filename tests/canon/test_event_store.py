import os
import tempfile
import pytest
from hypothesis import given, strategies as st
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
