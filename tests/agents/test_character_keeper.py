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
    def __init__(self, out):
        self._out = out
        self.calls = []

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


async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mara", traits="wary"))
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, personality="A protective, watchful presence.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A protective, watchful presence." in sent
    assert "In character:" in sent


async def test_commit_emits_remark_when_feed_note_present(stack):
    events, proj, read, committer = stack
    out = KeeperOutput(feed_note="Mara's arc is bending toward trust.")
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.commit(out, {"characters": [], "recent": []})
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "Mara's arc is bending toward trust."


async def test_updates_character_voice_and_leaves_unset_voice_unchanged(stack):
    events, proj, read, committer = stack
    await events.append(
        EventType.CHARACTER_CREATED, "c1",
        Character(id="c1", name="Mira", traits="stoic", arc_status="wary", voice="Speaks in short, clipped sentences."),
    )
    await proj.catch_up()

    # First update: voice is set explicitly and should change.
    out = KeeperOutput(updated_characters=[
        CharacterUpdate(id="c1", voice="Now trails off mid-sentence when scared."),
    ])
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    mira = await read.get_character("c1")
    assert mira.voice == "Now trails off mid-sentence when scared."
    assert mira.traits == "stoic"  # untouched field unaffected

    # Second update: voice left None should not clobber the existing voice.
    out2 = KeeperOutput(updated_characters=[CharacterUpdate(id="c1", arc_status="cracking")])
    agent2 = CharacterKeeper(FakeRunner(out2), read, committer)
    await agent2.run_once()
    await proj.catch_up()
    mira2 = await read.get_character("c1")
    assert mira2.voice == "Now trails off mid-sentence when scared."
    assert mira2.arc_status == "cracking"
