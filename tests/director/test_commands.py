import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import SignalKind
from novelizer.director import commands


class FakeScheduler:
    def __init__(self): self.paused = set()
    def pause_agent(self, n): self.paused.add(n)
    def resume_agent(self, n): self.paused.discard(n)


class FakeRuntime:
    def __init__(self, events, scheduler): self.events = events; self.scheduler = scheduler


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_seed_appends_signal(stack):
    events, proj, read = stack
    await commands.seed(events, "a storm is coming")
    await proj.catch_up()
    sigs = await read.list_unconsumed_signals()
    assert len(sigs) == 1 and sigs[0].kind == SignalKind.seed and "storm" in sigs[0].body


async def test_focus_appends_focus_signal(stack):
    events, proj, read = stack
    await commands.focus(events, "Mira")
    await proj.catch_up()
    sigs = await read.list_unconsumed_signals()
    assert sigs[0].kind == SignalKind.focus and sigs[0].body == "Mira"


async def test_dispatch_routes_and_reports(stack):
    events, proj, read = stack
    sched = FakeScheduler()
    rt = FakeRuntime(events, sched)
    assert "seed" in (await commands.dispatch(rt, "seed a storm")).lower()
    await proj.catch_up()
    assert len(await read.list_unconsumed_signals()) == 1
    await commands.dispatch(rt, "pause editor")
    assert "editor" in sched.paused
    await commands.dispatch(rt, "resume editor")
    assert "editor" not in sched.paused
    assert "unknown" in (await commands.dispatch(rt, "frobnicate x")).lower()
