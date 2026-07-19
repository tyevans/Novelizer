import os
import pytest
import tempfile
from novelizer.store.embeddings import EmbeddingStore, SearchHit
from novelizer.store.models import WorldEntry, ThemeRecord, ThreadRecord, SecretRecord, Chapter, Character
from tests.conftest import FakeEmbeddingFunction


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as d:
        base_url = os.environ.get("NOVELIZER_LLM_BASE_URL") or "http://192.168.1.14:8080/v1"
        s = EmbeddingStore(path=d, embed_model="nomic-embed-text", base_url=base_url)
        yield s
        s.close()


@pytest.fixture
async def fake_store(tmp_path):
    s = EmbeddingStore(path=str(tmp_path), embedding_function=FakeEmbeddingFunction())
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


async def test_upsert_and_delete_thread_and_secret(fake_store):
    await fake_store.upsert_thread(ThreadRecord(id="t1", name="Bell's Curse", last_note="rang again"))
    await fake_store.upsert_secret(SecretRecord(id="s1", title="The Scar"))
    assert fake_store._threads.count() == 1
    assert fake_store._secrets.count() == 1
    await fake_store.delete("t1", "threads")
    await fake_store.delete("s1", "secrets")
    assert fake_store._threads.count() == 0
    assert fake_store._secrets.count() == 0


async def test_search_merges_kinds_sorted_by_distance(fake_store):
    await fake_store.upsert_chapter(Chapter(id="ch1", title="The Drowned Bell", prose="The bell rang over the water."))
    await fake_store.upsert_character(Character(id="mara", name="Mara", traits="bell-ringer"))
    await fake_store.upsert_secret(SecretRecord(id="s1", title="The bell is cracked"))
    hits = await fake_store.search("bell", n=10)
    assert len(hits) == 3
    assert [type(h) for h in hits] == [SearchHit] * 3
    assert {(h.kind, h.id) for h in hits} == {("chapter", "ch1"), ("character", "mara"), ("secret", "s1")}
    assert hits == sorted(hits, key=lambda h: h.distance)


async def test_search_kind_filter_and_empty(fake_store):
    await fake_store.upsert_chapter(Chapter(id="ch1", title="One", prose="alpha beta"))
    only_secrets = await fake_store.search("alpha", kinds=["secret"])
    assert only_secrets == []
    only_chapters = await fake_store.search("alpha", kinds=["chapter"])
    assert [h.id for h in only_chapters] == ["ch1"]


async def test_search_unknown_kind_raises(fake_store):
    with pytest.raises(ValueError):
        await fake_store.search("x", kinds=["novel"])


async def test_concurrent_writes_are_serialized_and_complete(fake_store):
    import asyncio
    chapters = [Chapter(id=f"ch{i}", title=f"T{i}", prose="p") for i in range(8)]
    chars = [Character(id=f"c{i}", name=f"N{i}") for i in range(8)]
    await asyncio.gather(
        *[fake_store.upsert_chapter(c) for c in chapters],
        *[fake_store.upsert_character(c) for c in chars],
    )
    assert fake_store._chapters.count() == 8
    assert fake_store._chars.count() == 8
    assert fake_store._write_lock.locked() is False
