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
    tool = build_search_canon_tool(store, read, None)
    out = await tool.ainvoke({"query": "bell"})
    assert "(chapter) /chapters/001-the-drowned-bell.md" in out
    assert "[id: ch1]" in out


async def test_search_canon_kind_filter_and_no_results(store):
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)
    tool = build_search_canon_tool(store, FakeReadStore(chapters=[ch]), None)
    assert await tool.ainvoke({"query": "alpha", "kinds": ["secret"]}) == "No results."


async def test_search_canon_empty_index_reads_as_unavailable_not_as_a_miss(store):
    # Nothing indexed: "No results." would be a lie the agent acts on -- it
    # rephrases and retries forever instead of falling back to ls/glob/grep.
    tool = build_search_canon_tool(store, FakeReadStore(), None)
    out = await tool.ainvoke({"query": "the locket"})
    assert out.startswith("Search unavailable")
    assert "index is empty" in out
    assert "ls/glob/grep" in out
    assert "No results." not in out


async def test_search_canon_genuine_miss_on_populated_index_still_says_no_results(store):
    # The distinction is the point: a populated index that simply has nothing
    # for this query must NOT tell the agent search is broken.
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)
    tool = build_search_canon_tool(store, FakeReadStore(chapters=[ch]), None)
    out = await tool.ainvoke({"query": "alpha", "kinds": ["theme", "arc"]})
    assert out == "No results."


async def test_search_canon_unavailable_on_store_error(store):
    class Boom:
        async def search(self, *a, **k): raise RuntimeError("down")

    tool = build_search_canon_tool(Boom(), FakeReadStore(), None)
    out = await tool.ainvoke({"query": "x"})
    assert out.startswith("Search unavailable (RuntimeError)")
    assert "ls/glob/grep" in out


async def test_search_canon_unknown_kind_returns_corrective_feedback(store):
    tool = build_search_canon_tool(store, FakeReadStore(), None)
    out = await tool.ainvoke({"query": "x", "kinds": ["chapters"]})
    assert "Unknown kinds" in out and "chapter" in out
    assert not out.startswith("Search unavailable")


async def test_search_canon_tool_metadata():
    tool = build_search_canon_tool(None, None, None)
    assert tool.name == "search_canon"
    assert "canon" in tool.description.lower()


async def test_search_canon_promise_hit_points_at_ledger(store):
    from novelizer.store.models import PromiseRecord
    promise = PromiseRecord(id="p1", name="Sealed Letter", description="bell wax")
    await store.upsert_promise(promise)
    tool = build_search_canon_tool(store, FakeReadStore(), None)
    out = await tool.ainvoke({"query": "bell", "kinds": ["promise"]})
    assert "(promise) /outline/ledger.md — 'Sealed Letter' [id: p1]" in out


async def test_search_canon_brief_hit_points_at_briefs_dir(store):
    from novelizer.store.models import ChapterBriefRecord
    brief = ChapterBriefRecord(id="b1", target_ordinal=2, goal="bell tolls", synopsis="dusk")
    await store.upsert_brief(brief)
    tool = build_search_canon_tool(store, FakeReadStore(), None)
    out = await tool.ainvoke({"query": "bell", "kinds": ["brief"]})
    assert "(brief) /outline/briefs/ — 'bell tolls' [id: b1]" in out


async def test_search_canon_arc_hit_has_no_file(store):
    from novelizer.store.models import ArcRecord
    arc = ArcRecord(id="arc1", character_id="mara", arc_type="positive", lie="bells lie")
    await store.upsert_arc(arc)
    tool = build_search_canon_tool(store, FakeReadStore(), None)
    out = await tool.ainvoke({"query": "bell", "kinds": ["arc"]})
    assert "(arc) (no file — cite id) — 'Arc: mara' [id: arc1]" in out


async def test_entity_hit_inlines_name_type_description_and_relations(monkeypatch):
    class FakeHit:
        kind = "entity"
        id = "42"
        title = "The Salted Gull"
        distance = 0.1

    class FakeEmbeddingStore:
        async def search(self, query, kinds=None):
            return [FakeHit()]

    class FakeReadStoreLocal:
        async def list_chapters(self): return []
        async def list_characters(self): return []
        async def list_world_entries(self): return []
        async def list_threads(self): return []
        async def list_secrets(self): return []
        async def list_themes(self): return []

    class FakeKGStore:
        async def get_entity(self, entity_id):
            assert entity_id == 42
            return {"id": 42, "name": "The Salted Gull", "entity_type": "location",
                    "description": "a dockside tavern"}
        async def entity_relations(self, entity_id):
            return [{"relation_type": "frequented_by", "other_name": "Mateo", "direction": "in"}]

    tool = build_search_canon_tool(FakeEmbeddingStore(), FakeReadStoreLocal(), FakeKGStore())

    result = await tool.ainvoke({"query": "tavern"})

    assert "(entity)" in result
    assert "The Salted Gull" in result
    assert "location" in result
    assert "a dockside tavern" in result
    assert "frequented_by Mateo" in result
