import tempfile
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.models import ThemeRecord
from novelizer.brain.theme_similarity import suggest_near_duplicate_theme
from tests.conftest import FakeEmbeddingFunction


async def test_suggest_near_duplicate_theme_finds_similar_title():
    with tempfile.TemporaryDirectory() as d:
        store = EmbeddingStore(path=d, embedding_function=FakeEmbeddingFunction())
        await store.upsert_theme(ThemeRecord(id="loss", title="The Cost of Ambition"))
        suggestion = await suggest_near_duplicate_theme(
            store, ThemeRecord(id="loss2", title="The Price of Ambition")
        )
        assert suggestion == "loss"
        store.close()


async def test_suggest_near_duplicate_theme_returns_none_for_dissimilar_title():
    with tempfile.TemporaryDirectory() as d:
        store = EmbeddingStore(path=d, embedding_function=FakeEmbeddingFunction())
        await store.upsert_theme(ThemeRecord(id="loss", title="The Cost of Ambition"))
        suggestion = await suggest_near_duplicate_theme(
            store, ThemeRecord(id="joy2", title="A Blossoming Friendship")
        )
        assert suggestion is None
        store.close()


async def test_suggest_near_duplicate_theme_returns_none_when_no_themes():
    with tempfile.TemporaryDirectory() as d:
        store = EmbeddingStore(path=d, embedding_function=FakeEmbeddingFunction())
        suggestion = await suggest_near_duplicate_theme(
            store, ThemeRecord(id="loss", title="The Cost of Ambition")
        )
        assert suggestion is None
        store.close()
