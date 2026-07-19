import pytest

from novelizer.canon_fs.search import build_search_canon_tool
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.models import Chapter, Character, SecretRecord
from tests.conftest import FakeEmbeddingFunction


class FakeReadStore:
    """Just enough ReadStore for path resolution."""

    def __init__(self, chapters=(), characters=(), secrets=()):
        self._chapters, self._characters, self._secrets = (
            list(chapters), list(characters), list(secrets))

    async def list_chapters(self, status=None): return self._chapters
    async def list_characters(self): return self._characters
    async def list_world_entries(self, domain=None): return []
    async def list_threads(self): return []
    async def list_secrets(self): return self._secrets
    async def list_themes(self): return []


@pytest.fixture
def store(tmp_path):
    return EmbeddingStore(str(tmp_path / "emb"), embedding_function=FakeEmbeddingFunction())


async def test_search_canon_formats_hits_with_path_and_id(store):
    ch = Chapter(id="ch1", title="The Drowned Bell", prose="The bell rang.")
    await store.upsert_chapter(ch)
    read = FakeReadStore(chapters=[ch])
    tool = build_search_canon_tool(store, read)
    out = await tool.ainvoke({"query": "bell"})
    assert "(chapter) /chapters/001-the-drowned-bell.md" in out
    assert "[id: ch1]" in out


async def test_search_canon_kind_filter_and_no_results(store):
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)
    tool = build_search_canon_tool(store, FakeReadStore(chapters=[ch]))
    assert await tool.ainvoke({"query": "alpha", "kinds": ["secret"]}) == "No results."


async def test_search_canon_unavailable_on_store_error(store):
    class Boom:
        async def search(self, *a, **k): raise RuntimeError("down")

    tool = build_search_canon_tool(Boom(), FakeReadStore())
    out = await tool.ainvoke({"query": "x"})
    assert out.startswith("Search unavailable (RuntimeError)")
    assert "ls/glob/grep" in out


async def test_search_canon_unknown_kind_returns_corrective_feedback(store):
    tool = build_search_canon_tool(store, FakeReadStore())
    out = await tool.ainvoke({"query": "x", "kinds": ["chapters"]})
    assert "Unknown kinds" in out and "chapter" in out
    assert not out.startswith("Search unavailable")


async def test_search_canon_tool_metadata():
    tool = build_search_canon_tool(None, None)
    assert tool.name == "search_canon"
    assert "canon" in tool.description.lower()
