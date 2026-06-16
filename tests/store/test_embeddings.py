import pytest
import tempfile
import os
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.models import WorldEntry


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as d:
        s = EmbeddingStore(path=d, embed_model="nomic-embed-text")
        yield s
        s.close()


@pytest.mark.ollama
async def test_upsert_and_query(store):
    entry = WorldEntry(title="The Ashfields", body="A blasted plain south of the empire.")
    await store.upsert_world_entry(entry)
    results = await store.query_world_entries("southern wasteland", n=1)
    assert len(results) == 1
    assert results[0].title == "The Ashfields"


@pytest.mark.ollama
async def test_delete(store):
    entry = WorldEntry(title="Old place", body="It was there once.")
    await store.upsert_world_entry(entry)
    await store.delete(entry.id, collection="world_entries")
    results = await store.query_world_entries("old place", n=5)
    assert len(results) == 0
