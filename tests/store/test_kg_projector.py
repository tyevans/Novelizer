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
    hits = await emb.search("Salted Gull", kinds=["entity"])
    assert hits == []


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
    hits = await emb.search("Salted Gull", kinds=["entity"])
    assert hits == []


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
    """The whole-room deadlock regression. KG extraction is an LLM call, so it
    is the likeliest thing in the room to fail identically on every retry --
    and under the strict background gate a pinned KG cursor pauses every agent
    forever."""
    events, projector, read, kg, emb = wiring
    await seed_chapters(events, projector, "ch1", "ch2", "ch3")
    runner = PoisonExtractionRunner({"ch2": None})
    kgp = KGProjector(events, read, kg, emb, runner, str(tmp_path / "kg_cursor.json"),
                      poison_skip_after=3)

    assert await kgp.catch_up() == 1  # ch1 extracted, then ch2 fails (attempt 1)
    assert await kgp.catch_up() == 0  # ch2 fails again (attempt 2)
    caplog.clear()
    with caplog.at_level("ERROR"):
        # Attempt 3 exhausts the budget: ch2 is abandoned and ch3 -- stuck
        # behind it until now -- is extracted in the same pass. A skipped
        # event is not a processed one, so only ch3 counts here.
        assert await kgp.catch_up() == 1
    assert [r for r in caplog.records if r.levelname == "ERROR"], \
        "abandoning an event is data loss; it must be logged at ERROR, not warned about"

    assert runner.attempts["ch2"] == 3
    assert runner.attempts["ch3"] == 1

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
