import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationDrawn, InspirationHandConsumed
from novelizer.agents.character_keeper import CharacterKeeper
from novelizer.agents.schemas import KeeperOutput, NewCharacter
from novelizer.store.models import Chapter


class FakeRunner:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs):
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def _seed_consumed_hand(events, proj):
    await events.append(EventType.CHAPTER_CREATED, "c1",
                        Chapter(id="c1", title="One", prose="Doris crossed the yard."))
    await events.append(EventType.INSPIRATION_DRAWN, "h1", InspirationDrawn(
        hand_id="h1", seed=7, corpus_version="2026.07", era="modern",
        names=["Doris Kimbrough"], professions=["glazier"], settings=["salvage yard"],
        beats=["a debt is called in early"],
    ))
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, "h1",
                        InspirationHandConsumed(hand_id="h1", chapter_id="c1"))
    await proj.catch_up()


async def test_minting_a_dealt_name_records_uptake(stack):
    events, proj, read, committer = stack
    await _seed_consumed_hand(events, proj)
    out = KeeperOutput(new_characters=[NewCharacter(name="Doris Kimbrough")])
    await CharacterKeeper(FakeRunner(out), read, committer, events).run_once()
    await proj.catch_up()
    rows = await read.list_uptake("h1")
    assert [(r.kind, r.item, r.chapter_id) for r in rows] == [("names", "Doris Kimbrough", "c1")]


async def test_minting_an_undealt_name_records_nothing(stack):
    events, proj, read, committer = stack
    await _seed_consumed_hand(events, proj)
    out = KeeperOutput(new_characters=[NewCharacter(name="Prudence Vann")])
    await CharacterKeeper(FakeRunner(out), read, committer, events).run_once()
    await proj.catch_up()
    assert await read.list_uptake() == []
