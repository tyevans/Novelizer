import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Flag
from novelizer.canon.events import EventType
from novelizer.tui.widgets.escalations_model import escalated_flags, escalation_timeline


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_escalated_flags_returns_only_escalated(stack):
    events, proj, read = stack
    open_flag = Flag(id="f1", category="contradiction", description="normal")
    esc_flag = Flag(id="f2", category="contradiction", description="critical one", escalated=True, severity="critical")
    await events.append(EventType.FLAG_CREATED, "f1", open_flag)
    await events.append(EventType.FLAG_CREATED, "f2", esc_flag)
    await events.append(EventType.FLAG_ESCALATED, "f2", esc_flag)
    await proj.catch_up()

    result = await escalated_flags(read)
    assert [f.id for f in result] == ["f2"]


async def test_escalation_timeline_orders_events(stack):
    events, proj, read = stack
    flag = Flag(id="f3", category="contradiction", description="x", escalated=True, severity="critical")
    await events.append(EventType.FLAG_CREATED, "f3", flag)
    await events.append(EventType.FLAG_ESCALATED, "f3", flag)
    await proj.catch_up()

    timeline = await escalation_timeline(events, flag.id)
    assert [e.event_type for e in timeline] == [EventType.FLAG_CREATED, EventType.FLAG_ESCALATED]
