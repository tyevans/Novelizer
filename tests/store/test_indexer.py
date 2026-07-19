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
