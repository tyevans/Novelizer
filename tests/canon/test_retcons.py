import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import RetconRequest, RetconStatus, Character


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_retcon_created_then_resolved(stack):
    events, proj, read = stack
    req = RetconRequest(id="r1", description="scar hand mismatch",
                        conflicting_entry_ids=["a", "b"], proposed_resolution="left hand")
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1", req)
    await proj.catch_up()
    assert [r.id for r in await read.list_retcon_requests(status=RetconStatus.open)] == ["r1"]
    resolved = req.model_copy(update={"status": RetconStatus.resolved, "resolved_by": "retconner"})
    await events.append(EventType.RETCON_REQUEST_RESOLVED, "r1", resolved)
    await proj.catch_up()
    assert await read.list_retcon_requests(status=RetconStatus.open) == []
    assert [r.id for r in await read.list_retcon_requests(status=RetconStatus.resolved)] == ["r1"]


async def test_get_character(stack):
    events, proj, read = stack
    await events.append(EventType.CHARACTER_CREATED, "c1", Character(id="c1", name="Mira", arc_status="wary"))
    await proj.catch_up()
    got = await read.get_character("c1")
    assert got is not None and got.name == "Mira" and got.arc_status == "wary"
    assert await read.get_character("nope") is None
