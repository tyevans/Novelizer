import os
import tempfile

import pytest

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, SecretCreated, ThemeIntroduced, ThreadPlanted
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.indexer import CanonIndexer
from novelizer.store.models import Chapter, Character, WorldEntry
from tests.conftest import FakeEmbeddingFunction


@pytest.fixture
async def stack(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    store = EmbeddingStore(str(tmp_path / "emb"), embedding_function=FakeEmbeddingFunction())
    indexer = CanonIndexer(events, read, store, str(tmp_path / "cursor.json"))
    yield events, proj, read, store, indexer
    await read.close(); await proj.close(); await events.close()
    os.unlink(path)


async def seed(events, proj):
    await events.append(EventType.CHAPTER_CREATED, "ch1",
                        Chapter(id="ch1", title="One", prose="The bell rang."))
    await events.append(EventType.CHARACTER_CREATED, "mara",
                        Character(id="mara", name="Mara"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1",
                        WorldEntry(id="w1", title="Bell Cult", body="dusk"))
    await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="Curse"))
    await events.append(EventType.SECRET_CREATED, "s1", SecretCreated(id="s1", title="Scar"))
    await events.append(EventType.THEME_INTRODUCED, "th1", ThemeIntroduced(id="th1", title="Memory"))
    await proj.catch_up()


async def test_backfill_indexes_every_kind(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    processed = await indexer.catch_up()
    assert processed == 6
    hits = await store.search("bell", n=20)
    assert {h.kind for h in hits} == {"chapter", "character", "world", "thread", "secret", "theme"}


async def test_catch_up_is_incremental_and_idempotent(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    assert await indexer.catch_up() == 6
    assert await indexer.catch_up() == 0  # cursor persisted, nothing new
    await events.append(EventType.CHAPTER_CREATED, "ch2",
                        Chapter(id="ch2", title="Two", prose="More prose."))
    await proj.catch_up()
    assert await indexer.catch_up() == 1


async def test_cursor_survives_new_indexer_instance(stack, tmp_path):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.catch_up()
    fresh = CanonIndexer(events, read, store, str(tmp_path / "cursor.json"))
    assert await fresh.catch_up() == 0


async def test_embed_failure_leaves_cursor_for_retry(stack, tmp_path):
    events, proj, read, store, indexer = stack
    await seed(events, proj)

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("endpoint down")

    broken = CanonIndexer(events, read, Boom(), str(tmp_path / "cursor2.json"))
    assert await broken.catch_up() == 0  # swallowed, not raised
    assert await indexer.catch_up() == 6  # untouched cursor path still backfills


async def test_catch_up_never_raises_even_if_event_store_fails(stack, tmp_path):
    events, proj, read, store, indexer = stack

    class BrokenEvents:
        async def events_since(self, *a, **k): raise RuntimeError("database is locked")

    broken = CanonIndexer(BrokenEvents(), read, store, str(tmp_path / "c3.json"))
    assert await broken.catch_up() == 0


async def test_superseded_world_entry_removed_from_index(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.catch_up()
    hits = await store.search("bell", kinds=["world"], n=20)
    assert any(h.id == "w1" for h in hits)

    # aggregate_id is the entity the event concerns for indexing purposes --
    # here, the entry being superseded (w1). The payload is the replacement
    # record (w2), whose own upsert is driven by its own WORLD_ENTRY_CREATED
    # trail; this event's job is to retire w1 from the active list/index.
    await events.append(
        EventType.WORLD_ENTRY_SUPERSEDED, "w1",
        WorldEntry(id="w2", title="Bell Cult Revised", body="dawn", supersedes_id="w1"),
    )
    await proj.catch_up()
    await indexer.catch_up()

    assert store._world.get(ids=["w1"])["ids"] == []
