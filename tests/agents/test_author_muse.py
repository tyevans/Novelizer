import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationDrawn
from novelizer.agents.author import Author, AUTHOR_SYSTEM_PROMPT, ChapterDraft
from novelizer.agents.world_architect import WorldArchitect
from novelizer.agents.schemas import WorldEntriesDraft
from novelizer.store.models import Chapter, DirectorSignal, HandStatus, SignalKind


class FakeRunner:
    def __init__(self, draft): self._draft = draft; self.calls = []
    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._draft}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


def _drawn(hand_id="h1"):
    return InspirationDrawn(
        hand_id=hand_id, seed=7, corpus_version="2026.07", era="modern",
        names=["Doris Kimbrough"], professions=["glazier"],
        settings=["salvage yard"], beats=["a debt is called in early"],
    )


async def test_author_prompt_carries_pool_and_sparks(stack):
    events, proj, read, committer = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    await Author(runner, read, committer).run_once()
    content = runner.calls[0]["messages"][0]["content"]
    assert "Doris Kimbrough" in content and "a debt is called in early" in content


async def test_author_consumes_hand_on_new_chapter(stack):
    events, proj, read, committer = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await proj.catch_up()
    await Author(FakeRunner(ChapterDraft(title="T", prose="P")), read, committer).run_once()
    await proj.catch_up()
    hand = await read.get_hand("h1")
    chapter = (await read.list_chapters())[0]
    assert hand.status == HandStatus.consumed and hand.consumed_chapter_id == chapter.id


async def test_author_revision_does_not_consume_hand(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="orig"))
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.revise, body="fix",
                                        target_agent="author", target_entity="c1"))
    await proj.catch_up()
    await Author(FakeRunner(ChapterDraft(title="One", prose="new")), read, committer).run_once()
    await proj.catch_up()
    assert (await read.get_hand("h1")).status == HandStatus.active


async def test_author_works_without_a_hand(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    await Author(runner, read, committer).run_once()
    await proj.catch_up()
    assert len(await read.list_chapters()) == 1
    assert "Casting pool" not in runner.calls[0]["messages"][0]["content"]


async def test_system_prompt_bans_ai_tells():
    assert "Elias" in AUTHOR_SYSTEM_PROMPT and "lighthouse" in AUTHOR_SYSTEM_PROMPT


async def test_world_architect_sees_setting_sparks_but_never_consumes(stack):
    events, proj, read, committer = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await proj.catch_up()
    runner = FakeRunner(WorldEntriesDraft(entries=[]))
    await WorldArchitect(runner, read, committer).run_once()
    await proj.catch_up()
    assert "salvage yard" in runner.calls[0]["messages"][0]["content"]
    assert (await read.get_hand("h1")).status == HandStatus.active
