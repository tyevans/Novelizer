import asyncio
import contextlib

import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.kg_store import KGStore
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.kg_projector import KGProjector
from tests.conftest import FakeEmbeddingFunction


class FakeExtractionRunner:
    """Stands in for build_kg_extraction_runner's graph: same .ainvoke shape
    ContinuityChecker's mining runner uses, returning a canned
    KGExtractionOutput via structured_response. `output` may be a single
    KGExtractionOutput (returned for every call) or a list of them (returned
    in order, one per call, to simulate one call per prose chunk)."""

    def __init__(self, output):
        self._output = output
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        if isinstance(self._output, list):
            return {"structured_response": self._output[self.calls - 1]}
        return {"structured_response": self._output}


class PoisonExtractionRunner:
    """Extraction runner that raises for chosen chapters instead of returning
    facts. `failures` maps chapter title -> how many ainvoke calls raise
    before that chapter starts succeeding; None means every call raises, i.e.
    a genuine poison chapter (the seq-33 mid-JSON truncation in
    kg_projector.py's docstring is exactly this shape). `attempts` tallies
    extraction calls per chapter title."""

    def __init__(self, failures: dict[str, int | None], output=None) -> None:
        self._failures = failures
        self._output = output
        self.attempts: dict[str, int] = {}

    async def ainvoke(self, messages):
        # kg_extraction_prompt renders "Chapter: <title>\n\n<prose>".
        prompt = messages["messages"][0]["content"]
        title = prompt.split("\n", 1)[0].removeprefix("Chapter: ")
        self.attempts[title] = self.attempts.get(title, 0) + 1
        budget = self._failures.get(title, 0)
        if budget is None or self.attempts[title] <= budget:
            raise RuntimeError("structured output truncated mid-JSON")
        return {"structured_response": self._output}


async def seed_chapters(events, projector, *chapter_ids):
    for chapter_id in chapter_ids:
        await events.append_raw(EventType.CHAPTER_CREATED, chapter_id, {
            "id": chapter_id, "title": chapter_id, "prose": f"Eldara walks the docks of {chapter_id}.",
        })
    await projector.catch_up()


# --- Phase 5: parallel-drain probes -----------------------------------------
#
# The KG drain gains the same shape as the canon indexer's: dedupe by aggregate,
# partition, drain partitions concurrently under shared-pool permits, advance the
# cursor over the longest contiguous success prefix. KG extraction is an LLM call
# (kg_runner), so every probe below drives the FAKE runner -- never a real
# network call -- and each chapter's seeded prose is short enough to be a single
# extraction chunk, so one _index_chapter == one ainvoke.


def _title_of(messages) -> str:
    # kg_extraction_prompt renders "Chapter: <title>\n\n<prose>".
    return messages["messages"][0]["content"].split("\n", 1)[0].removeprefix("Chapter: ")


class ConcurrencyProbeRunner:
    """Extraction runner recording per-title call counts and the PEAK number of
    ainvoke calls in flight at once (one per partition under parallel drain). A
    serial drain never sees peak > 1. Yields after registering itself so
    overlapping calls are observed by scheduling order, not wall-clock racing."""

    def __init__(self, output) -> None:
        self._output = output
        self.calls_by_title: dict[str, int] = {}
        self.live = 0
        self.peak = 0

    async def ainvoke(self, messages):
        title = _title_of(messages)
        self.calls_by_title[title] = self.calls_by_title.get(title, 0) + 1
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(0)
            return {"structured_response": self._output}
        finally:
            self.live -= 1


class BarrierExtractionRunner:
    """Holds every ainvoke until `parties` of them are in flight at once, then
    releases them together. A serial catch_up can never get two extractions in
    flight, so `all_entered` never fires and a test awaiting it times out -- the
    red state a serial loop should produce for 'chapters extract concurrently'."""

    def __init__(self, parties: int, output) -> None:
        self._parties = parties
        self._output = output
        self.entered = 0
        self.all_entered = asyncio.Event()
        self._release = asyncio.Event()

    async def ainvoke(self, messages):
        self.entered += 1
        if self.entered >= self._parties:
            self.all_entered.set()
        await self._release.wait()
        return {"structured_response": self._output}

    def release(self) -> None:
        self._release.set()


class SpyPool:
    """Minimal AdaptivePool stand-in: enforces a hard concurrency ceiling like
    the real pool's `_limit`, records peak permits held, and tallies the AIMD
    signals the drain feeds back, so the 429 -> note_rate_limited / clean ->
    note_success mapping (agent_kit/scheduler.py `_run`) can be asserted. The KG
    drain must draw from a pool it duck-types; agent_kit stays canon-agnostic."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._sem = asyncio.Semaphore(limit)
        self.live = 0
        self.peak = 0
        self.rate_limited = 0
        self.successes = 0

    @contextlib.asynccontextmanager
    async def slot(self):
        async with self._sem:
            self.live += 1
            self.peak = max(self.peak, self.live)
            try:
                yield
            finally:
                self.live -= 1

    def note_rate_limited(self) -> None:
        self.rate_limited += 1

    def note_success(self) -> None:
        self.successes += 1


class RateLimitError(Exception):
    """Named so agent_kit.base._is_rate_limit_error matches it by class name."""


@pytest.fixture
async def wiring(tmp_path):
    db_path = str(tmp_path / "world.db")
    events = EventStore(db_path)
    await events.init()
    projector = Projector(events, db_path)
    await projector.init()
    read = ReadStore(db_path)
    await read.init()
    kg = KGStore(db_path)
    await kg.init()
    emb = EmbeddingStore(str(tmp_path / "chroma"), embedding_function=FakeEmbeddingFunction())
    yield events, projector, read, kg, emb
    await events.close()
    await projector.close()
    await read.close()
    await kg.close()


@pytest.mark.asyncio
async def test_catch_up_extracts_structured_character_facts(wiring, tmp_path):
    events, projector, read, kg, emb = wiring
    await events.append_raw(EventType.CHARACTER_CREATED, "c1", {
        "id": "c1", "name": "Eldara", "traits": "sharp-tongued",
    })
    await projector.catch_up()

    from novelizer.agents.schemas import KGExtractionOutput
    runner = FakeExtractionRunner(KGExtractionOutput())
    cursor_path = str(tmp_path / "kg_cursor.json")
    kgp = KGProjector(events, read, kg, emb, runner, cursor_path)

    processed = await kgp.catch_up()

    assert processed == 1
    entity = await kg.find_entity_by_name("Eldara", "character")
    assert entity is not None
    assert entity["canon_id"] == "c1"
    hits = await emb.search("Eldara", kinds=["entity"])
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_chapter_revised_reflows_prose_extracted_entities(wiring, tmp_path):
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput, KGExtractedEntity

    await events.append_raw(EventType.CHAPTER_CREATED, "ch1", {
        "id": "ch1", "title": "Ch 1", "prose": "Eldara enters The Salted Gull.",
    })
    await projector.catch_up()

    runner = FakeExtractionRunner(KGExtractionOutput(
        entities=[KGExtractedEntity(name="The Salted Gull", entity_type="location")],
    ))
    cursor_path = str(tmp_path / "kg_cursor.json")
    kgp = KGProjector(events, read, kg, emb, runner, cursor_path)
    await kgp.catch_up()
    assert (await kg.find_entity_by_name("The Salted Gull", "location")) is not None

    # Revise the chapter with prose that no longer mentions the tavern
    await events.append_raw(EventType.CHAPTER_REVISED, "ch1", {
        "chapter_id": "ch1", "title": "Ch 1", "prose": "Eldara stays home.",
    })
    await projector.catch_up()
    runner._output = KGExtractionOutput(entities=[])  # nothing extracted this time
    await kgp.catch_up()

    # Reflow cleared the mention; the embedding was deleted, though the
    # kg_entities row itself can remain (harmless orphan row, out of scope
    # to garbage-collect per this task -- see docstring in kg_projector.py).
    # The tavern's vector was the only document in the index, so deleting it
    # empties the index; search() reports an empty index as unavailable rather
    # than answering, so assert on the count instead of querying.
    assert await emb.document_count() == 0


@pytest.mark.asyncio
async def test_long_chapter_is_extracted_in_chunks_and_merged(wiring, tmp_path):
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput, KGExtractedEntity, KGExtractedRelation

    long_prose = "word " * 2000  # well past _EXTRACTION_CHUNK_CHARS (3000) -> 4 chunks
    await events.append_raw(EventType.CHAPTER_CREATED, "ch1", {
        "id": "ch1", "title": "Ch 1", "prose": long_prose,
    })
    await projector.catch_up()

    # Overlapping/duplicate entity and relation across chunks to verify merge dedupes.
    dup_output = KGExtractionOutput(
        entities=[
            KGExtractedEntity(name="Eldara", entity_type="character"),
            KGExtractedEntity(name="The Salted Gull", entity_type="location"),
        ],
        relations=[KGExtractedRelation(source="Eldara", target="The Salted Gull", relation_type="frequents")],
    )
    runner = FakeExtractionRunner([
        KGExtractionOutput(
            entities=[KGExtractedEntity(name="Eldara", entity_type="character", description="a sailor")],
            relations=[KGExtractedRelation(source="Eldara", target="The Salted Gull", relation_type="frequents")],
        ),
        dup_output, dup_output, dup_output,
    ])
    cursor_path = str(tmp_path / "kg_cursor.json")
    kgp = KGProjector(events, read, kg, emb, runner, cursor_path)
    await kgp.catch_up()

    assert runner.calls == 4  # one call per chunk
    eldara = await kg.find_entity_by_name("Eldara", "character")
    assert eldara is not None
    assert eldara["description"] == "a sailor"  # first non-empty description wins
    gull = await kg.find_entity_by_name("The Salted Gull", "location")
    assert gull is not None
    relations = await kg.entity_relations(eldara["id"])
    assert len(relations) == 1  # duplicate relation across chunks merged to one


@pytest.mark.asyncio
async def test_chapter_revised_keeps_entity_mentioned_in_another_chapter(wiring, tmp_path):
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput, KGExtractedEntity

    await events.append_raw(EventType.CHAPTER_CREATED, "ch1", {
        "id": "ch1", "title": "Ch 1", "prose": "Eldara enters The Salted Gull.",
    })
    await events.append_raw(EventType.CHAPTER_CREATED, "ch2", {
        "id": "ch2", "title": "Ch 2", "prose": "The Salted Gull burns down.",
    })
    await projector.catch_up()

    gull = KGExtractedEntity(name="The Salted Gull", entity_type="location")
    runner = FakeExtractionRunner(KGExtractionOutput(entities=[gull]))
    cursor_path = str(tmp_path / "kg_cursor.json")
    kgp = KGProjector(events, read, kg, emb, runner, cursor_path)
    await kgp.catch_up()  # indexes both ch1 and ch2, each mentioning the tavern

    entity = await kg.find_entity_by_name("The Salted Gull", "location")
    assert entity is not None
    entity_id = entity["id"]

    # Revise ch1 to drop the tavern; ch2 still mentions it, so the entity
    # must survive (both in kg_entity_mentions and the embedding).
    await events.append_raw(EventType.CHAPTER_REVISED, "ch1", {
        "chapter_id": "ch1", "title": "Ch 1", "prose": "Eldara stays home.",
    })
    await projector.catch_up()
    runner._output = KGExtractionOutput(entities=[])
    await kgp.catch_up()

    assert await kg.has_mentions(entity_id) is True
    hits = await emb.search("Salted Gull", kinds=["entity"])
    assert len(hits) == 1

    # Now revise ch2 to also drop it -- only then should it disappear.
    await events.append_raw(EventType.CHAPTER_REVISED, "ch2", {
        "chapter_id": "ch2", "title": "Ch 2", "prose": "Nothing burns.",
    })
    await projector.catch_up()
    await kgp.catch_up()

    assert await kg.has_mentions(entity_id) is False
    # Its vector was the last document in the index (see the sibling reflow
    # test): an empty index is unavailable, not queryable, so count instead.
    assert await emb.document_count() == 0


@pytest.mark.asyncio
async def test_chapter_revised_reflows_prose_extracted_relation(wiring, tmp_path):
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput, KGExtractedEntity, KGExtractedRelation

    await events.append_raw(EventType.CHAPTER_CREATED, "ch1", {
        "id": "ch1", "title": "Ch 1", "prose": "Eldara distrusts Kessa.",
    })
    await projector.catch_up()

    with_relation = KGExtractionOutput(
        entities=[
            KGExtractedEntity(name="Eldara", entity_type="character"),
            KGExtractedEntity(name="Kessa", entity_type="character"),
        ],
        relations=[
            KGExtractedRelation(source="Eldara", target="Kessa", relation_type="distrusts"),
        ],
    )
    runner = FakeExtractionRunner(with_relation)
    cursor_path = str(tmp_path / "kg_cursor.json")
    kgp = KGProjector(events, read, kg, emb, runner, cursor_path)
    await kgp.catch_up()

    eldara = await kg.find_entity_by_name("Eldara", "character")
    kessa = await kg.find_entity_by_name("Kessa", "character")
    assert eldara is not None and kessa is not None
    relations = await kg.entity_relations(eldara["id"])
    assert any(r["relation_type"] == "distrusts" and r["other_name"] == "Kessa" for r in relations)

    # Revise the chapter so the relation no longer holds; both entities
    # still appear, but without the relation between them.
    await events.append_raw(EventType.CHAPTER_REVISED, "ch1", {
        "chapter_id": "ch1", "title": "Ch 1", "prose": "Eldara and Kessa never meet.",
    })
    await projector.catch_up()
    runner._output = KGExtractionOutput(
        entities=[
            KGExtractedEntity(name="Eldara", entity_type="character"),
            KGExtractedEntity(name="Kessa", entity_type="character"),
        ],
        relations=[],
    )
    await kgp.catch_up()

    relations_after = await kg.entity_relations(eldara["id"])
    assert not any(r["relation_type"] == "distrusts" for r in relations_after)

    # A relation that survives the revision (re-extracted each time) keeps working.
    runner._output = with_relation
    await events.append_raw(EventType.CHAPTER_REVISED, "ch1", {
        "chapter_id": "ch1", "title": "Ch 1", "prose": "Eldara distrusts Kessa still.",
    })
    await projector.catch_up()
    await kgp.catch_up()

    relations_survived = await kg.entity_relations(eldara["id"])
    assert any(r["relation_type"] == "distrusts" and r["other_name"] == "Kessa" for r in relations_survived)


@pytest.mark.asyncio
async def test_prose_extracted_entity_links_to_existing_character(wiring, tmp_path):
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput, KGExtractedEntity

    await events.append_raw(EventType.CHARACTER_CREATED, "c1", {
        "id": "c1", "name": "Eldara", "traits": "sharp-tongued",
    })
    await events.append_raw(EventType.CHAPTER_CREATED, "ch1", {
        "id": "ch1", "title": "Ch 1", "prose": "Eldara walks the docks.",
    })
    await projector.catch_up()

    # Extraction LLM labels her "person" (not "character"), so this lands as
    # a separate kg_entities row from the structured-source one -- but it
    # should still get canon_id linked back to c1.
    runner = FakeExtractionRunner(KGExtractionOutput(
        entities=[KGExtractedEntity(name="Eldara", entity_type="person")],
    ))
    cursor_path = str(tmp_path / "kg_cursor.json")
    kgp = KGProjector(events, read, kg, emb, runner, cursor_path)
    await kgp.catch_up()

    prose_entity = await kg.find_entity_by_name("Eldara", "person")
    assert prose_entity is not None
    assert prose_entity["canon_id"] == "c1"


@pytest.mark.asyncio
async def test_lag_reports_kg_indexable_events_not_yet_caught_up(wiring, tmp_path):
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput

    await events.append_raw(EventType.CHARACTER_CREATED, "c1", {"id": "c1", "name": "Eldara"})
    await projector.catch_up()
    kgp = KGProjector(events, read, kg, emb, FakeExtractionRunner(KGExtractionOutput()),
                      str(tmp_path / "kg_cursor.json"))

    assert await kgp.lag() == 1  # nothing extracted yet
    await kgp.catch_up()
    assert await kgp.lag() == 0  # fully caught up

    await events.append_raw(EventType.CHAPTER_CREATED, "ch1", {
        "id": "ch1", "title": "Ch 1", "prose": "Eldara walks the docks.",
    })
    await projector.catch_up()
    assert await kgp.lag() == 1
    assert await kgp.lag() == 1  # read-only: calling again doesn't change it


@pytest.mark.asyncio
async def test_lag_does_not_mutate_the_kg_cursor(wiring, tmp_path):
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput

    await events.append_raw(EventType.CHARACTER_CREATED, "c1", {"id": "c1", "name": "Eldara"})
    await projector.catch_up()
    cursor_path = str(tmp_path / "kg_cursor.json")
    kgp = KGProjector(events, read, kg, emb, FakeExtractionRunner(KGExtractionOutput()), cursor_path)

    await kgp.lag()  # must not advance the cursor
    fresh = KGProjector(events, read, kg, emb, FakeExtractionRunner(KGExtractionOutput()), cursor_path)
    assert await fresh.catch_up() == 1  # still unextracted


@pytest.mark.asyncio
async def test_kg_lag_ignores_events_only_the_canon_indexer_cares_about(wiring, tmp_path):
    """The KG projects 6 event types against CanonIndexer's 24, so the two lags
    are not interchangeable -- the background gate has to consult both."""
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput
    from novelizer.canon.events import ThreadPlanted
    from novelizer.store.indexer import CanonIndexer

    await events.append_raw(EventType.CHARACTER_CREATED, "c1", {"id": "c1", "name": "Eldara"})
    await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="Curse"))
    await projector.catch_up()

    kgp = KGProjector(events, read, kg, emb, FakeExtractionRunner(KGExtractionOutput()),
                      str(tmp_path / "kg_cursor.json"))
    indexer = CanonIndexer(events, read, emb, str(tmp_path / "canon_cursor.json"))

    assert await kgp.lag() == 1      # the character only
    assert await indexer.lag() == 2  # the character AND the thread


@pytest.mark.asyncio
async def test_poison_chapter_is_skipped_after_the_configured_attempt_budget(wiring, tmp_path, caplog):
    """The whole-room deadlock regression, now under PARALLEL drain. KG
    extraction is an LLM call, so it is the likeliest thing in the room to fail
    identically on every retry -- and under the strict background gate a pinned
    KG cursor pauses every agent forever.

    REWRITTEN for Phase 5 (was a serial-loop test). The old version asserted
    `attempts["ch3"] == 1` -- an artifact of the serial loop leaving ch3 stuck
    BEHIND poison ch2. Under parallel drain ch3 is a different aggregate and
    extracts CONCURRENTLY on pass 1; because the cursor may not advance past
    failed ch2 (property C), ch3 is redundantly (idempotently) re-extracted each
    pass, so `attempts["ch3"]` climbs. Invariant, and what this now pins: the
    cursor never passes ch2 until its budget is spent, ch2 is tried EXACTLY
    poison_skip_after times, and the backlog drains. The per-pass processed
    counts (1, 0, 1) are unchanged -- processed counts the contiguous success
    PREFIX in sequences, which ch3's early extract sits beyond."""
    events, projector, read, kg, emb = wiring
    await seed_chapters(events, projector, "ch1", "ch2", "ch3")
    runner = PoisonExtractionRunner({"ch2": None})
    kgp = KGProjector(events, read, kg, emb, runner, str(tmp_path / "kg_cursor.json"),
                      poison_skip_after=3)

    # Pass 1: ch1 (seq 1) is the whole contiguous success prefix. ch3 (seq 3)
    # extracts concurrently even though the cursor is pinned at 1 by ch2.
    assert await kgp.catch_up() == 1
    assert kgp._load_cursor() == 1
    assert runner.attempts.get("ch3", 0) >= 1  # extracted despite the pinned cursor

    assert await kgp.catch_up() == 0  # ch2 fails again (attempt 2); cursor still 1
    assert kgp._load_cursor() == 1
    caplog.clear()
    with caplog.at_level("ERROR"):
        # Attempt 3 exhausts the budget: ch2 abandoned, cursor jumps past ch2
        # and ch3 at once. A skipped event is not processed, so only ch3 counts.
        assert await kgp.catch_up() == 1
    assert [r for r in caplog.records if r.levelname == "ERROR"], \
        "abandoning an event is data loss; it must be logged at ERROR, not warned about"

    assert runner.attempts["ch2"] == 3  # exactly the budget, never a 4th try

    assert await kgp.catch_up() == 0
    assert runner.attempts["ch2"] == 3
    assert await kgp.lag() == 0


@pytest.mark.asyncio
async def test_transient_extraction_failure_is_retried_rather_than_skipped(wiring, tmp_path, caplog):
    events, projector, read, kg, emb = wiring
    await seed_chapters(events, projector, "ch1", "ch2", "ch3")
    runner = PoisonExtractionRunner({"ch2": 2})
    kgp = KGProjector(events, read, kg, emb, runner, str(tmp_path / "kg_cursor.json"),
                      poison_skip_after=3)

    caplog.clear()
    with caplog.at_level("ERROR"):
        assert await kgp.catch_up() == 1  # ch1 extracted, ch2 fails (attempt 1)
        assert await kgp.catch_up() == 0  # ch2 fails (attempt 2)
        assert await kgp.catch_up() == 2  # ch2 succeeds on attempt 3, then ch3
    assert not [r for r in caplog.records if r.levelname == "ERROR"]

    assert runner.attempts["ch2"] == 3
    assert await kgp.lag() == 0


@pytest.mark.asyncio
async def test_kg_failure_counts_are_per_sequence_and_do_not_accumulate(wiring, tmp_path, caplog):
    """Three chapters each failing once must not add up to a skip: the budget
    is per sequence, and a sequence's tally clears when it succeeds."""
    events, projector, read, kg, emb = wiring
    await seed_chapters(events, projector, "ch1", "ch2", "ch3")
    runner = PoisonExtractionRunner({"ch1": 1, "ch2": 1, "ch3": 1})
    kgp = KGProjector(events, read, kg, emb, runner, str(tmp_path / "kg_cursor.json"),
                      poison_skip_after=2)

    caplog.clear()
    with caplog.at_level("ERROR"):
        for _ in range(6):
            await kgp.catch_up()
    assert not [r for r in caplog.records if r.levelname == "ERROR"]

    assert runner.attempts == {"ch1": 2, "ch2": 2, "ch3": 2}  # one failure, one success each
    assert await kgp.lag() == 0


@pytest.mark.asyncio
async def test_kg_catch_up_drains_the_backlog_even_when_every_extraction_fails(wiring, tmp_path):
    """Nothing succeeds and nothing raises, yet the backlog still reaches zero
    -- the property the strict background gate stakes the whole room on."""
    events, projector, read, kg, emb = wiring
    await seed_chapters(events, projector, "ch1", "ch2", "ch3")
    runner = PoisonExtractionRunner({"ch1": None, "ch2": None, "ch3": None})
    kgp = KGProjector(events, read, kg, emb, runner, str(tmp_path / "kg_cursor.json"),
                      poison_skip_after=2)

    for _ in range(3 * 3):
        await kgp.catch_up()

    assert await kgp.lag() == 0


@pytest.mark.asyncio
async def test_kg_catch_up_never_raises_even_if_event_store_fails(wiring, tmp_path):
    events, projector, read, kg, emb = wiring

    class BrokenEvents:
        async def events_since(self, *a, **k): raise RuntimeError("database is locked")
        async def count_since(self, *a, **k): raise RuntimeError("database is locked")

    kgp = KGProjector(BrokenEvents(), read, kg, emb, PoisonExtractionRunner({}),
                      str(tmp_path / "kg_cursor.json"), poison_skip_after=3)
    assert await kgp.catch_up() == 0


# --- Phase 5, property A: dedupe by aggregate within the pending window -----


@pytest.mark.asyncio
async def test_multiple_events_for_one_chapter_extract_it_once_per_pass(wiring, tmp_path):
    """Chapter 7 revised three times is four events, four sequences, ONE
    aggregate. The projector re-extracts the CURRENT chapter regardless of which
    event triggered it, and extraction is the most expensive call in the room, so
    the drain must collapse the window to a single extraction. processed still
    counts all four consumed sequences."""
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput

    await events.append_raw(EventType.CHAPTER_CREATED, "ch7", {
        "id": "ch7", "title": "ch7", "prose": "Eldara v0 at the docks.",
    })
    for v in range(1, 4):
        await events.append_raw(EventType.CHAPTER_REVISED, "ch7", {
            "chapter_id": "ch7", "title": "ch7", "prose": f"Eldara v{v} at the docks.",
        })
    await projector.catch_up()

    runner = ConcurrencyProbeRunner(KGExtractionOutput())
    kgp = KGProjector(events, read, kg, emb, runner, str(tmp_path / "kg_cursor.json"))
    processed = await kgp.catch_up()

    assert runner.calls_by_title.get("ch7", 0) == 1  # one extraction for the whole window
    assert processed == 4                            # all four sequences consumed
    assert await kgp.lag() == 0


# --- Phase 5, property B: partitions for different chapters extract concurrently


@pytest.mark.asyncio
async def test_partitions_for_different_chapters_extract_concurrently(wiring, tmp_path):
    """Two distinct chapters must both reach 'inside the extraction call' before
    either finishes. The barrier releases only once BOTH extractions are in
    flight; a serial catch_up holds one at a time, so the wait times out. Under
    the strict gate the drain is the room's critical path, so a serial KG drain
    just becomes the new idleness."""
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput

    await seed_chapters(events, projector, "ch1", "ch2")
    runner = BarrierExtractionRunner(parties=2, output=KGExtractionOutput())
    kgp = KGProjector(events, read, kg, emb, runner, str(tmp_path / "kg_cursor.json"),
                      drain_concurrency=4)

    task = asyncio.create_task(kgp.catch_up())
    try:
        await asyncio.wait_for(runner.all_entered.wait(), timeout=2.0)
    finally:
        runner.release()
    assert await task == 2


# --- Phase 5, property C: cursor == longest contiguous success prefix -------


@pytest.mark.asyncio
async def test_kg_cursor_stops_just_before_the_first_failed_chapter(wiring, tmp_path):
    """Sequences [1..3]; ch2 fails forever, ch1 and ch3 succeed. The cursor may
    advance only to 1: ch3 extracts successfully (concurrent drain) but advancing
    past failed ch2 would lose its facts, so the cursor pins at 1 and ch2,ch3 are
    retried next pass. poison_skip_after is large so nothing is abandoned here."""
    events, projector, read, kg, emb = wiring
    await seed_chapters(events, projector, "ch1", "ch2", "ch3")
    runner = PoisonExtractionRunner({"ch2": None})  # output=None -> ch1,ch3 succeed empty
    kgp = KGProjector(events, read, kg, emb, runner, str(tmp_path / "kg_cursor.json"),
                      poison_skip_after=99)
    processed = await kgp.catch_up()

    assert kgp._load_cursor() == 1  # just before ch2 (seq 2)
    assert processed == 1
    assert runner.attempts.get("ch3", 0) >= 1  # ch3 extracted despite the pin


# --- Phase 5, property D: poison-skip unblocks the prefix under parallelism --


@pytest.mark.asyncio
async def test_kg_permanently_failing_chapter_is_skipped_so_the_prefix_advances(wiring, tmp_path, caplog):
    """Property D at the KG drain level. A chapter that fails extraction every
    pass sits at seq 2 with good work behind and ahead of it. Under the strict
    gate a cursor wedged at seq 1 forever pauses every agent forever, so after
    poison_skip_after passes ch2 must be abandoned and the cursor jump past it."""
    events, projector, read, kg, emb = wiring
    await seed_chapters(events, projector, "ch1", "ch2", "ch3")
    runner = PoisonExtractionRunner({"ch2": None})
    kgp = KGProjector(events, read, kg, emb, runner, str(tmp_path / "kg_cursor.json"),
                      poison_skip_after=3)

    await kgp.catch_up()
    assert kgp._load_cursor() == 1
    # ch3 extracted on pass 1 despite the cursor being wedged at 1 by ch2 -- the
    # parallel signature; a serial loop breaks at ch2 and never reaches ch3.
    assert runner.attempts.get("ch3", 0) >= 1
    await kgp.catch_up()
    assert kgp._load_cursor() == 1
    caplog.clear()
    with caplog.at_level("ERROR"):
        await kgp.catch_up()  # attempt 3 exhausts the budget on ch2
    assert [r for r in caplog.records if r.levelname == "ERROR"]

    assert runner.attempts["ch2"] == 3
    assert await kgp.lag() == 0  # cursor jumped past ch2 and drained ch3


# --- Phase 5: shared-pool integration (ceiling + AIMD signal mapping) --------


@pytest.mark.asyncio
async def test_kg_drain_units_respect_the_shared_pool_ceiling(wiring, tmp_path):
    """KG extraction draws permits from the SAME AdaptivePool the scheduler and
    the canon indexer use, so total endpoint load (agents + both drains) respects
    one ceiling. With four pending chapters and a pool limit of 2, at most two
    extractions run at once though drain_concurrency would allow all four."""
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput

    await seed_chapters(events, projector, "ch1", "ch2", "ch3", "ch4")
    runner = ConcurrencyProbeRunner(KGExtractionOutput())
    pool = SpyPool(limit=2)
    kgp = KGProjector(events, read, kg, emb, runner, str(tmp_path / "kg_cursor.json"),
                      pool=pool, drain_concurrency=4)

    processed = await kgp.catch_up()
    assert processed == 4
    assert pool.peak == 2    # the shared ceiling bit
    assert runner.peak <= 2  # no extraction ran without a permit


@pytest.mark.asyncio
async def test_kg_drain_maps_a_rate_limit_to_note_rate_limited(wiring, tmp_path):
    """A 429 during KG extraction must feed the shared pool the same congestion
    signal an agent-run 429 does, so AIMD backoff covers the KG drain -- the past
    429 pile-ups came from this consumer having no shared ceiling at all."""
    events, projector, read, kg, emb = wiring
    await seed_chapters(events, projector, "ch1")

    class RateLimitedRunner:
        async def ainvoke(self, messages):
            raise RateLimitError("429 slow down")

    pool = SpyPool(limit=4)
    kgp = KGProjector(events, read, kg, emb, RateLimitedRunner(),
                      str(tmp_path / "kg_cursor.json"), pool=pool)
    await kgp.catch_up()

    assert pool.rate_limited == 1
    assert pool.successes == 0


@pytest.mark.asyncio
async def test_kg_drain_maps_a_clean_unit_to_note_success(wiring, tmp_path):
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput

    await seed_chapters(events, projector, "ch1", "ch2")
    runner = FakeExtractionRunner(KGExtractionOutput())
    pool = SpyPool(limit=4)
    kgp = KGProjector(events, read, kg, emb, runner, str(tmp_path / "kg_cursor.json"), pool=pool)
    await kgp.catch_up()

    assert pool.successes == 2  # one clean signal per successfully drained chapter
    assert pool.rate_limited == 0


@pytest.mark.asyncio
async def test_kg_drain_feeds_no_signal_on_a_plain_crash(wiring, tmp_path):
    """A malformed extraction response or a bug is not congestion; mirroring the
    scheduler, a non-429 crash feeds the pool neither signal."""
    events, projector, read, kg, emb = wiring
    await seed_chapters(events, projector, "ch1")

    class BuggyRunner:
        async def ainvoke(self, messages):
            raise RuntimeError("a bug, not a rate limit")

    pool = SpyPool(limit=4)
    kgp = KGProjector(events, read, kg, emb, BuggyRunner(),
                      str(tmp_path / "kg_cursor.json"), pool=pool)
    await kgp.catch_up()

    assert pool.rate_limited == 0
    assert pool.successes == 0
