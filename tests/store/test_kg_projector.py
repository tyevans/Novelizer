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
    KGExtractionOutput via structured_response."""

    def __init__(self, output):
        self._output = output
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        return {"structured_response": self._output}


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
