import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter, WorldEntry, Character, DirectorSignal, SignalKind


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close()
    os.unlink(path)


async def test_chapter_visible_after_projection(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    chapters = await read.list_chapters()
    assert [c.title for c in chapters] == ["One"]
    assert (await read.get_chapter("c1")).prose == "p"


async def test_unconsumed_signals_filtered_by_target(stack):
    events, proj, read = stack
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.seed, body="broadcast"))
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s2",
                        DirectorSignal(id="s2", kind=SignalKind.focus, body="for-editor", target_agent="editor"))
    await proj.catch_up()
    for_author = await read.list_unconsumed_signals(target_agent="author")
    assert {s.id for s in for_author} == {"s1"}  # broadcast only, not editor-targeted


async def test_consumed_signal_disappears(stack):
    events, proj, read = stack
    sig = DirectorSignal(id="s1", kind=SignalKind.seed, body="x")
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1", sig)
    await events.append(EventType.DIRECTOR_SIGNAL_CONSUMED, "s1", sig)
    await proj.catch_up()
    assert await read.list_unconsumed_signals() == []
