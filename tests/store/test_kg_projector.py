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
