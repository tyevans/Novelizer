import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.character_keeper import CharacterKeeper
from novelizer.agents.schemas import KeeperOutput, CharacterUpdate, RetconDraft
from novelizer.store.models import Character, Chapter, RetconStatus


class FakeRunner:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_updates_character_arc_and_files_retcon(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "c1", Character(id="c1", name="Mira", traits="stoic", arc_status="wary"))
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Mira wept openly."))
    await proj.catch_up()
    out = KeeperOutput(
        updated_characters=[CharacterUpdate(id="c1", arc_status="cracking")],
        retcon_requests=[RetconDraft(description="stoic vs weeping", conflicting_entry_ids=["c1", "ch1"], proposed_resolution="show restraint")],
    )
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    mira = await read.get_character("c1")
    assert mira.arc_status == "cracking" and mira.name == "Mira" and mira.traits == "stoic"
    assert len(await read.list_retcon_requests(status=RetconStatus.open)) == 1


async def test_noop_when_no_characters(stack):
    events, proj, read, committer = stack
    agent = CharacterKeeper(FakeRunner(KeeperOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_characters() == []
