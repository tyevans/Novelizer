import os
import pytest
import tempfile
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.models import WorldEntry, ThemeRecord, ThreadRecord, SecretRecord
from tests.conftest import FakeEmbeddingFunction


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as d:
        base_url = os.environ.get("NOVELIZER_LLM_BASE_URL") or "http://192.168.1.14:8080/v1"
        s = EmbeddingStore(path=d, embed_model="nomic-embed-text", base_url=base_url)
        yield s
        s.close()


def test_embedding_store_accepts_injectable_embedding_function(tmp_path):
    store = EmbeddingStore(path=str(tmp_path), embedding_function=FakeEmbeddingFunction())
    store.close()


async def test_upsert_and_query_themes_roundtrip(tmp_path):
    store = EmbeddingStore(path=str(tmp_path), embedding_function=FakeEmbeddingFunction())
    await store.upsert_theme(ThemeRecord(id="loss", title="The Cost of Ambition"))
    results = await store.query_themes("The Cost of Ambition")
    assert results and results[0][0] == "loss"
    store.close()


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


async def test_upsert_and_delete_thread_and_secret():
    with tempfile.TemporaryDirectory() as tmp_path:
        store = EmbeddingStore(path=tmp_path, embedding_function=FakeEmbeddingFunction())
        await store.upsert_thread(ThreadRecord(id="t1", name="Bell's Curse", last_note="rang again"))
        await store.upsert_secret(SecretRecord(id="s1", title="The Scar"))
        assert store._threads.count() == 1
        assert store._secrets.count() == 1
        await store.delete("t1", "threads")
        await store.delete("s1", "secrets")
        assert store._threads.count() == 0
        assert store._secrets.count() == 0
        store.close()
