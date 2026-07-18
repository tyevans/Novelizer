import os
import pytest
import tempfile
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.models import WorldEntry


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as d:
        base_url = os.environ.get("NOVELIZER_LLM_BASE_URL") or "http://192.168.1.14:8080/v1"
        s = EmbeddingStore(path=d, embed_model="nomic-embed-text", base_url=base_url)
        yield s
        s.close()


@pytest.mark.live_llm
async def test_upsert_and_query(store):
    entry = WorldEntry(title="The Ashfields", body="A blasted plain south of the empire.")
    await store.upsert_world_entry(entry)
    results = await store.query_world_entries("southern wasteland", n=1)
    assert len(results) == 1
    assert results[0].title == "The Ashfields"


@pytest.mark.live_llm
async def test_delete(store):
    entry = WorldEntry(title="Old place", body="It was there once.")
    await store.upsert_world_entry(entry)
    await store.delete(entry.id, collection="world_entries")
    results = await store.query_world_entries("old place", n=5)
    assert len(results) == 0
