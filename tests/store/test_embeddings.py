import os
import pytest
import tempfile
import time
from novelizer.store.embeddings import (
    EmbeddingStore, EmbedProbeFailure, EmptyIndexError, SearchHit,
)
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


async def test_search_on_empty_index_raises_rather_than_reporting_a_miss(fake_store):
    # Nothing indexed at all: the store cannot answer the question, and saying
    # so is the difference between the caller reporting "unavailable" and
    # reporting "canon contains nothing on this topic".
    with pytest.raises(EmptyIndexError):
        await fake_store.search("anything")


async def test_search_unknown_kind_still_wins_over_empty_index(fake_store):
    # Corrective kind feedback is actionable; the emptiness report is not, so
    # validation stays ahead of the emptiness check even on a dead index.
    with pytest.raises(ValueError):
        await fake_store.search("x", kinds=["novel"])


async def test_document_count_totals_every_collection(fake_store):
    assert await fake_store.document_count() == 0
    await fake_store.upsert_secret(SecretRecord(id="s1", title="Scar"))
    await fake_store.upsert_theme(ThemeRecord(id="th1", title="Memory"))
    assert await fake_store.document_count() == 2


async def test_upsert_chapter_chunks_oversized_prose(fake_store):
    from novelizer.store.embeddings import _CHAPTER_CHUNK_CHARS
    huge_prose = "word " * 5000  # far past a single chunk
    await fake_store.upsert_chapter(Chapter(id="ch1", title="Huge", prose=huge_prose))
    chunks = fake_store._chapters.get(where={"chapter_id": "ch1"})
    assert len(chunks["ids"]) > 1
    assert all(len(doc) <= _CHAPTER_CHUNK_CHARS for doc in chunks["documents"])
    # reassembled (minus overlap) chunks reconstruct the original prose
    assert "".join(chunks["documents"])[: len(huge_prose)].startswith(huge_prose[:100])


async def test_upsert_chapter_revision_drops_stale_trailing_chunks(fake_store):
    long_prose = "word " * 5000
    await fake_store.upsert_chapter(Chapter(id="ch1", title="Long", prose=long_prose))
    before = fake_store._chapters.get(where={"chapter_id": "ch1"})
    assert len(before["ids"]) > 1
    await fake_store.upsert_chapter(Chapter(id="ch1", title="Short", prose="just a short revision"))
    after = fake_store._chapters.get(where={"chapter_id": "ch1"})
    assert len(after["ids"]) == 1


async def test_delete_chapter_removes_all_chunks(fake_store):
    huge_prose = "word " * 5000
    await fake_store.upsert_chapter(Chapter(id="ch1", title="Huge", prose=huge_prose))
    assert fake_store._chapters.count() > 1
    await fake_store.delete("ch1", "chapters")
    assert fake_store._chapters.count() == 0


async def test_search_dedupes_chapter_chunks_to_one_hit(fake_store):
    huge_prose = "the bell rang " + "word " * 5000 + "the bell rang again"
    await fake_store.upsert_chapter(Chapter(id="ch1", title="Huge", prose=huge_prose))
    hits = await fake_store.search("bell", kinds=["chapter"])
    assert [h.id for h in hits] == ["ch1"]


async def test_query_chapters_dedupes_and_hydrates_base_id(fake_store):
    huge_prose = "the bell rang " + "word " * 5000 + "the bell rang again"
    await fake_store.upsert_chapter(Chapter(id="ch1", title="Huge", prose=huge_prose))
    results = await fake_store.query_chapters("bell")
    assert [c.id for c in results] == ["ch1"]


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


@pytest.mark.asyncio
async def test_entity_upsert_is_searchable_as_entity_kind(tmp_path):
    store = EmbeddingStore(str(tmp_path / "chroma"), embedding_function=FakeEmbeddingFunction())

    await store.upsert_entity("42", "The Salted Gull", "The Salted Gull [location] a dockside tavern")

    hits = await store.search("Salted Gull", kinds=["entity"])
    assert len(hits) == 1
    assert hits[0].kind == "entity"
    assert hits[0].id == "42"


@pytest.mark.asyncio
async def test_delete_entity_removes_it_from_search(tmp_path):
    store = EmbeddingStore(str(tmp_path / "chroma"), embedding_function=FakeEmbeddingFunction())
    await store.upsert_entity("42", "The Salted Gull", "a dockside tavern")
    # A second entity keeps the index populated, so this stays a test about
    # deletion rather than about the empty-index case search() now reports as
    # unavailable.
    await store.upsert_entity("43", "The Ashfields", "a blasted plain")

    await store.delete_entity("42")

    hits = await store.search("Salted Gull", kinds=["entity"])
    assert [h.id for h in hits] == ["43"]


# --- endpoint probe: one call that says WHY the index will stay empty --------
#
# A dead embedding endpoint is otherwise undetectable until it has already cost
# a day of runs: upsert failures are swallowed by the drain's never-raise
# contract, the poison ladder abandons each event after its budget, the cursor
# ends up past the whole backlog, and lag() then reads 0 forever. One probe at
# boot turns that into a single legible line.


class _RaisingEmbeddingFunction(FakeEmbeddingFunction):
    """An embed endpoint that fails the way a real one does. `exc` is raised on
    every call; chromadb's own protocol methods still work, so the store builds
    normally and only the probe (or a real upsert) trips it."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __call__(self, input):
        raise self._exc


class _HttpError(Exception):
    """Stands in for openai's APIStatusError family without importing it: the
    probe classifies by the duck-typed `status_code` every one of them carries,
    so it never depends on that SDK's exception hierarchy."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _EmptyEmbeddingFunction(FakeEmbeddingFunction):
    """A 200 response that carries no vectors -- the failure mode that raises
    nothing at all and so is invisible to any try/except."""

    def __call__(self, input):
        return []


class _HangingEmbeddingFunction(FakeEmbeddingFunction):
    def __call__(self, input):
        time.sleep(30)  # far past any probe timeout
        raise AssertionError("probe waited for a hung endpoint instead of timing out")


def _store(tmp_path, ef):
    return EmbeddingStore(str(tmp_path / "probe"), embed_model="nomic-embed-text",
                          base_url="http://embed.invalid/v1", embedding_function=ef)


async def test_probe_reports_ok_and_the_vector_width_on_a_live_endpoint(tmp_path):
    store = _store(tmp_path, FakeEmbeddingFunction())
    try:
        probe = await store.probe()
        assert probe.ok is True
        assert probe.failure is None
        assert probe.dimensions == FakeEmbeddingFunction._DIM
        assert probe.endpoint == "http://embed.invalid/v1"
        assert probe.model == "nomic-embed-text"
    finally:
        store.close()


async def test_probe_distinguishes_every_failure_mode(tmp_path):
    """One call, four distinguishable diagnoses. Reporting them as one
    undifferentiated "embedding failed" would leave the operator guessing
    between a down host, a typo'd model name and a bad key."""
    cases = [
        (_RaisingEmbeddingFunction(ConnectionError("connection refused")),
         EmbedProbeFailure.unreachable),
        (_RaisingEmbeddingFunction(_HttpError(404)), EmbedProbeFailure.no_such_model),
        (_RaisingEmbeddingFunction(_HttpError(401)), EmbedProbeFailure.unauthorized),
        (_RaisingEmbeddingFunction(_HttpError(403)), EmbedProbeFailure.unauthorized),
        (_RaisingEmbeddingFunction(_HttpError(500)), EmbedProbeFailure.http_error),
        (_EmptyEmbeddingFunction(), EmbedProbeFailure.no_vectors),
    ]
    for i, (ef, expected) in enumerate(cases):
        store = EmbeddingStore(str(tmp_path / f"probe{i}"), embedding_function=ef)
        try:
            probe = await store.probe()
            assert probe.ok is False
            assert probe.failure is expected, f"{ef!r} misclassified as {probe.failure}"
            assert probe.dimensions is None
        finally:
            store.close()


async def test_probe_carries_the_underlying_error_for_diagnosis(tmp_path):
    store = _store(tmp_path, _RaisingEmbeddingFunction(ConnectionError("connection refused")))
    try:
        probe = await store.probe()
        assert "ConnectionError" in probe.error
        assert "connection refused" in probe.error
    finally:
        store.close()


async def test_probe_is_bounded_and_never_hangs_on_a_dead_host(tmp_path):
    """An unreachable host can accept a TCP connection and then say nothing at
    all. Without a bound the probe would hold start() forever -- strictly worse
    than the dead index it is trying to report."""
    store = _store(tmp_path, _HangingEmbeddingFunction())
    try:
        began = time.monotonic()
        probe = await store.probe(timeout=0.25)
        elapsed = time.monotonic() - began
        assert probe.ok is False
        assert probe.failure is EmbedProbeFailure.timeout
        assert elapsed < 5, f"probe took {elapsed:.1f}s; the timeout did not bind"
    finally:
        store.close()


async def test_probe_adds_no_meaningful_latency_when_healthy(tmp_path):
    """One short string, one embed call: the healthy path must not be something
    an operator would notice at boot."""
    store = _store(tmp_path, FakeEmbeddingFunction())
    try:
        began = time.monotonic()
        assert (await store.probe()).ok is True
        assert time.monotonic() - began < 1.0
    finally:
        store.close()


class _ClientBackedEmbeddingFunction(FakeEmbeddingFunction):
    """Shaped like chromadb's OpenAIEmbeddingFunction: holds an openai client
    whose with_options() returns a reconfigured copy. `calls` is shared by
    reference so it still records after the probe shallow-copies the function."""

    class _Client:
        def __init__(self, options=None) -> None:
            self.max_retries = 2
            self.options = options

        def with_options(self, **kwargs):
            return _ClientBackedEmbeddingFunction._Client(options=kwargs)

    def __init__(self) -> None:
        self.client = self._Client()
        self.calls: list[dict | None] = []

    def __call__(self, input):
        self.calls.append(self.client.options)
        return super().__call__(input)


async def test_probe_makes_exactly_one_attempt_without_the_indexers_retries(tmp_path):
    """A probe asks "is it alive right now": one attempt. The embedding client
    retries twice with backoff by default, which is right for the INDEXER (a
    transient blip must not lose an embedding) and wrong here -- it turns an
    instant connection-refused into seconds of boot latency and reports nothing
    extra."""
    ef = _ClientBackedEmbeddingFunction()
    store = _store(tmp_path, ef)
    try:
        assert (await store.probe()).ok is True
        assert ef.calls == [{"max_retries": 0}], \
            f"probe did not disable retries for its one attempt: {ef.calls}"
        # The store's own embedding function is untouched, so real upserts keep
        # the retries they want.
        assert ef.client.max_retries == 2
        assert ef.client.options is None
    finally:
        store.close()


async def test_probe_still_works_when_the_embedding_function_has_no_client(tmp_path):
    """The no-retry optimisation is best-effort: any embedding function that does
    not expose an openai-style client is probed exactly as it is, rather than
    the probe failing over an attribute it merely hoped for."""
    store = _store(tmp_path, FakeEmbeddingFunction())  # no `.client` at all
    try:
        assert (await store.probe()).ok is True
    finally:
        store.close()
