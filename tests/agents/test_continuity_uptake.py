import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationDrawn, InspirationHandConsumed
from novelizer.agents.continuity_checker import ContinuityChecker
from novelizer.agents.schemas import ContinuityOutput, MinedFactsOutput, MinedInspirationFact
from novelizer.store.models import Chapter


class FakeRunner:
    def __init__(self, out): self._out = out; self.calls = []
    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def _seed(events, proj):
    await events.append(EventType.CHAPTER_CREATED, "c1",
                        Chapter(id="c1", title="One", prose="The glazier waited in the salvage yard."))
    await events.append(EventType.INSPIRATION_DRAWN, "h1", InspirationDrawn(
        hand_id="h1", seed=7, corpus_version="2026.07", era="modern",
        names=["Doris Kimbrough"], professions=["glazier"], settings=["salvage yard"],
        beats=["a debt is called in early"],
    ))
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, "h1",
                        InspirationHandConsumed(hand_id="h1", chapter_id="c1"))
    await proj.catch_up()


def _checker(read, committer, events, mining_out):
    return ContinuityChecker(
        FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events,
    )


async def test_mining_prompt_lists_dealt_items(stack):
    events, proj, read, committer = stack
    await _seed(events, proj)
    mining_runner = FakeRunner(MinedFactsOutput())
    checker = ContinuityChecker(FakeRunner(ContinuityOutput()), mining_runner, read, committer, events)
    await checker.run_once()
    prompt = mining_runner.calls[0]["messages"][0]["content"]
    assert "glazier" in prompt and "a debt is called in early" in prompt


async def test_valid_inspiration_fact_records_uptake_verbatim(stack):
    events, proj, read, committer = stack
    await _seed(events, proj)
    out = MinedFactsOutput(inspiration_facts=[
        MinedInspirationFact(kind="professions", item="GLAZIER"),  # case-insensitive match
    ])
    await _checker(read, committer, events, out).run_once()
    await proj.catch_up()
    rows = await read.list_uptake("h1")
    assert [(r.kind, r.item, r.chapter_id) for r in rows] == [("professions", "glazier", "c1")]


async def test_undealt_item_is_dropped_not_retconned(stack):
    events, proj, read, committer = stack
    await _seed(events, proj)
    out = MinedFactsOutput(inspiration_facts=[
        MinedInspirationFact(kind="beats", item="a completely invented beat"),
    ])
    await _checker(read, committer, events, out).run_once()
    await proj.catch_up()
    assert await read.list_uptake() == []
    assert await read.list_flags(category="contradiction", status="open") == []
