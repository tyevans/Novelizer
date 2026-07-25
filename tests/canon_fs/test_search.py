import pytest

import novelizer.canon_fs.search as search_mod
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
    out = await tool.ainvoke({"query": "bell", "purpose": "checking canon"})
    assert "(chapter) /chapters/001-the-drowned-bell.md" in out
    assert "[id: ch1]" in out


async def test_search_canon_kind_filter_and_no_results(store):
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)
    tool = build_search_canon_tool(store, FakeReadStore(chapters=[ch]), None)
    assert await tool.ainvoke({"query": "alpha", "purpose": "checking canon", "kinds": ["secret"]}) == "No results."


async def test_search_canon_empty_index_reads_as_unavailable_not_as_a_miss(store):
    # Nothing indexed: "No results." would be a lie the agent acts on -- it
    # rephrases and retries forever instead of falling back to ls/glob/grep.
    tool = build_search_canon_tool(store, FakeReadStore(), None)
    out = await tool.ainvoke({"query": "the locket", "purpose": "checking canon"})
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
    out = await tool.ainvoke({"query": "alpha", "purpose": "checking canon", "kinds": ["theme", "arc"]})
    assert out == "No results."


async def test_search_canon_unavailable_on_store_error(store):
    class Boom:
        async def search(self, *a, **k): raise RuntimeError("down")

    tool = build_search_canon_tool(Boom(), FakeReadStore(), None)
    out = await tool.ainvoke({"query": "x", "purpose": "checking canon"})
    assert out.startswith("Search unavailable (RuntimeError)")
    assert "ls/glob/grep" in out


async def test_search_canon_unknown_kind_returns_corrective_feedback(store):
    tool = build_search_canon_tool(store, FakeReadStore(), None)
    out = await tool.ainvoke({"query": "x", "purpose": "checking canon", "kinds": ["chapters"]})
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
    out = await tool.ainvoke({"query": "bell", "purpose": "checking canon", "kinds": ["promise"]})
    assert "(promise) /outline/ledger.md — 'Sealed Letter' [id: p1]" in out


async def test_search_canon_brief_hit_points_at_briefs_dir(store):
    from novelizer.store.models import ChapterBriefRecord
    brief = ChapterBriefRecord(id="b1", target_ordinal=2, goal="bell tolls", synopsis="dusk")
    await store.upsert_brief(brief)
    tool = build_search_canon_tool(store, FakeReadStore(), None)
    out = await tool.ainvoke({"query": "bell", "purpose": "checking canon", "kinds": ["brief"]})
    assert "(brief) /outline/briefs/ — 'bell tolls' [id: b1]" in out


async def test_search_canon_arc_hit_has_no_file(store):
    from novelizer.store.models import ArcRecord
    arc = ArcRecord(id="arc1", character_id="mara", arc_type="positive", lie="bells lie")
    await store.upsert_arc(arc)
    tool = build_search_canon_tool(store, FakeReadStore(), None)
    out = await tool.ainvoke({"query": "bell", "purpose": "checking canon", "kinds": ["arc"]})
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

    result = await tool.ainvoke({"query": "tavern", "purpose": "checking canon"})

    assert "(entity)" in result
    assert "The Salted Gull" in result
    assert "location" in result
    assert "a dockside tavern" in result
    assert "frequented_by Mateo" in result


class _SummarySettings:
    agent_model = "m"
    llm_base_url = "http://x"
    llm_api_key = "k"
    llm_max_tokens = 4096
    search_summarize = True


class _NullBackend:
    async def aread(self, path, offset=0, limit=2000):
        from deepagents.backends.protocol import ReadResult
        from deepagents.backends.utils import create_file_data
        return ReadResult(file_data=create_file_data("body text"))


async def _boom_summarize(*a, **k):
    raise AssertionError("summarizer must not run")


async def test_summarize_false_is_byte_identical_to_the_bare_hit_list(store, monkeypatch):
    """The regression anchor: opting out must reproduce the old output exactly."""
    ch = Chapter(id="ch1", title="The Drowned Bell", prose="The bell rang.")
    await store.upsert_chapter(ch)
    read = FakeReadStore(chapters=[ch])
    monkeypatch.setattr(
        search_mod.search_summary, "summarize", _boom_summarize)  # must never be called
    tool = build_search_canon_tool(
        store, read, None, backend=_NullBackend(),
        settings_provider=lambda: _SummarySettings())
    out = await tool.ainvoke(
        {"query": "bell", "purpose": "p", "summarize": False})
    assert out == "(chapter) /chapters/001-the-drowned-bell.md — 'The Drowned Bell' [id: ch1]"


async def test_kill_switch_off_skips_summarization(store, monkeypatch):
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)
    monkeypatch.setattr(search_mod.search_summary, "summarize", _boom_summarize)

    class _Off(_SummarySettings):
        search_summarize = False

    tool = build_search_canon_tool(
        store, FakeReadStore(chapters=[ch]), None, backend=_NullBackend(),
        settings_provider=lambda: _Off())
    out = await tool.ainvoke({"query": "alpha", "purpose": "p"})
    assert not out.startswith("CONTEXT")


async def test_no_settings_provider_skips_summarization(store, monkeypatch):
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)
    monkeypatch.setattr(search_mod.search_summary, "summarize", _boom_summarize)
    tool = build_search_canon_tool(store, FakeReadStore(chapters=[ch]), None)
    out = await tool.ainvoke({"query": "alpha", "purpose": "p"})
    assert not out.startswith("CONTEXT")


async def test_no_backend_skips_summarization(store, monkeypatch):
    """gather_excerpts has nothing to read from without a backend."""
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)
    monkeypatch.setattr(search_mod.search_summary, "summarize", _boom_summarize)
    tool = build_search_canon_tool(
        store, FakeReadStore(chapters=[ch]), None,
        settings_provider=lambda: _SummarySettings())
    out = await tool.ainvoke({"query": "alpha", "purpose": "p"})
    assert not out.startswith("CONTEXT")


async def test_summary_is_prepended_and_hit_lines_survive_verbatim(store, monkeypatch):
    ch = Chapter(id="ch1", title="The Drowned Bell", prose="The bell rang.")
    await store.upsert_chapter(ch)
    read = FakeReadStore(chapters=[ch])

    async def _fake_summarize(query, purpose, excerpts, settings, callbacks=None):
        return "The bell tolled at dusk."

    monkeypatch.setattr(search_mod.search_summary, "summarize", _fake_summarize)
    tool = build_search_canon_tool(
        store, read, None, backend=_NullBackend(),
        settings_provider=lambda: _SummarySettings())
    out = await tool.ainvoke({"query": "bell", "purpose": "deciding ch12"})
    assert out.startswith("CONTEXT (for: deciding ch12)")
    assert "The bell tolled at dusk." in out
    assert "RESULTS (cite these ids)" in out
    # the hit line is untouched
    assert "(chapter) /chapters/001-the-drowned-bell.md — 'The Drowned Bell' [id: ch1]" in out


async def test_summarizer_failure_degrades_to_the_bare_hit_list(store, monkeypatch):
    ch = Chapter(id="ch1", title="The Drowned Bell", prose="The bell rang.")
    await store.upsert_chapter(ch)

    async def _empty(query, purpose, excerpts, settings, callbacks=None):
        return ""

    monkeypatch.setattr(search_mod.search_summary, "summarize", _empty)
    tool = build_search_canon_tool(
        store, FakeReadStore(chapters=[ch]), None, backend=_NullBackend(),
        settings_provider=lambda: _SummarySettings())
    out = await tool.ainvoke({"query": "bell", "purpose": "p"})
    assert out == "(chapter) /chapters/001-the-drowned-bell.md — 'The Drowned Bell' [id: ch1]"
    assert "CONTEXT" not in out


async def test_early_returns_never_reach_the_summarizer(store, monkeypatch):
    """Empty index, no results, and store errors all short-circuit."""
    monkeypatch.setattr(search_mod.search_summary, "summarize", _boom_summarize)
    tool = build_search_canon_tool(
        store, FakeReadStore(), None, backend=_NullBackend(),
        settings_provider=lambda: _SummarySettings())
    out = await tool.ainvoke({"query": "anything", "purpose": "p"})
    assert out.startswith("Search unavailable")


async def test_kill_switch_is_read_at_call_time_not_construction(store, monkeypatch):
    """The runtime caches this tool for the process's lifetime, so a settings
    reload has to reach it without a rebuild."""
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)

    async def _fake_summarize(query, purpose, excerpts, settings, callbacks=None):
        return "summary"

    monkeypatch.setattr(search_mod.search_summary, "summarize", _fake_summarize)
    live = _SummarySettings()
    tool = build_search_canon_tool(
        store, FakeReadStore(chapters=[ch]), None, backend=_NullBackend(),
        settings_provider=lambda: live)
    assert (await tool.ainvoke({"query": "alpha", "purpose": "p"})).startswith("CONTEXT")
    live.search_summarize = False   # operator flips it; no rebuild
    assert not (await tool.ainvoke({"query": "alpha", "purpose": "p"})).startswith("CONTEXT")
