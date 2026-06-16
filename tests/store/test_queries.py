import pytest
import tempfile
import os
from novelizer.store.queries import Store
from novelizer.store.models import WorldEntry, Character, Chapter, DirectorSignal, SignalKind


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "world.db")
        chroma_path = os.path.join(d, "chroma")
        s = Store(db_path=db_path, chroma_path=chroma_path, embed_model="nomic-embed-text")
        await s.init()
        # Patch embedding methods to no-ops so tests don't need Ollama
        async def noop(*a, **kw):
            pass
        s.embeddings.upsert_world_entry = noop
        s.embeddings.upsert_character = noop
        s.embeddings.upsert_chapter = noop
        yield s
        await s.close()


async def test_save_and_list_world_entry(store):
    entry = WorldEntry(title="The Ashfields", body="Blasted plain.")
    await store.save_world_entry(entry)
    entries = await store.list_world_entries()
    assert any(e.title == "The Ashfields" for e in entries)


async def test_save_and_list_character(store):
    char = Character(name="Maren", traits="Brave")
    await store.save_character(char)
    chars = await store.list_characters()
    assert any(c.name == "Maren" for c in chars)


async def test_save_chapter_and_count_drafts(store):
    ch = Chapter(title="Ch 1", prose="She ran.")
    await store.save_chapter(ch)
    count = await store.db.count_draft_chapters()
    assert count == 1


async def test_director_signal_flow(store):
    sig = DirectorSignal(kind=SignalKind.seed, body="Empire falls.")
    await store.save_director_signal(sig)
    pending = await store.list_unconsumed_signals()
    assert len(pending) == 1
    await store.consume_signal(sig.id)
    pending = await store.list_unconsumed_signals()
    assert len(pending) == 0
