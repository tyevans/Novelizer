import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import Flag, FlagStatus


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_flag_created_projects_and_lists_by_category(stack):
    events, proj, read = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="pacing", description="Act 2 sags",
                             related_entry_ids=[], proposed_resolution="", filed_by="structure_analyst"))
    await proj.catch_up()
    flags = await read.list_flags(category="pacing", status=FlagStatus.open)
    assert len(flags) == 1
    assert flags[0].id == "f1"
    assert flags[0].filed_by == "structure_analyst"
    assert await read.list_flags(category="thematic") == []


async def test_flag_resolved_updates_status(stack):
    events, proj, read = stack
    f = Flag(id="f1", category="worldbuilding", description="no map of the north",
              related_entry_ids=[], proposed_resolution="")
    await events.append(EventType.FLAG_CREATED, "f1", f)
    await proj.catch_up()
    resolved = f.model_copy(update={"status": FlagStatus.resolved, "resolved_by": "world_architect"})
    await events.append(EventType.FLAG_RESOLVED, "f1", resolved)
    await proj.catch_up()
    assert await read.list_flags(status=FlagStatus.open) == []
    got = await read.list_flags(status=FlagStatus.resolved)
    assert len(got) == 1 and got[0].resolved_by == "world_architect"


async def test_legacy_retcon_request_created_event_aliases_into_flags(stack):
    """A pre-migration event log still has retcon_request.created events with
    no `category` key in the payload. The projector must alias these into the
    flags table as category="contradiction" so old databases keep working."""
    from novelizer.canon.events import EventType as ET
    events, proj, read = stack
    legacy_payload = {
        "id": "r1", "created_at": "2026-01-01T00:00:00+00:00",
        "description": "two vs one sun", "conflicting_entry_ids": ["w1"],
        "proposed_resolution": "one sun", "status": "open", "resolved_by": None,
    }
    await events.append_raw(ET.RETCON_REQUEST_CREATED, "r1", legacy_payload)
    await proj.catch_up()
    flags = await read.list_flags(category="contradiction", status="open")
    assert len(flags) == 1
    assert flags[0].id == "r1"
    assert flags[0].related_entry_ids == ["w1"]


async def test_retcon_requests_table_no_longer_created(stack):
    events, proj, read = stack
    cur = await read._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='retcon_requests'"
    )
    assert await cur.fetchone() is None


async def test_flag_escalated_sets_escalated_column(stack):
    events, proj, read = stack
    flag = Flag(id="f1", category="contradiction", description="x", severity="critical")
    await events.append(EventType.FLAG_CREATED, "f1", flag)
    escalated_flag = flag.model_copy(update={"escalated": True})
    await events.append(EventType.FLAG_ESCALATED, "f1", escalated_flag)
    await proj.catch_up()

    flags = await read.list_flags(escalated=True)
    assert len(flags) == 1
    assert flags[0].id == "f1"
    assert flags[0].severity == "critical"


async def test_flag_escalation_cleared_unsets_escalated_column(stack):
    events, proj, read = stack
    flag = Flag(id="f2", category="contradiction", description="x", severity="critical", escalated=True)
    await events.append(EventType.FLAG_CREATED, "f2", flag)
    await events.append(EventType.FLAG_ESCALATED, "f2", flag)
    cleared_flag = flag.model_copy(update={"escalated": False})
    await events.append(EventType.FLAG_ESCALATION_CLEARED, "f2", cleared_flag)
    await proj.catch_up()

    flags = await read.list_flags(escalated=True)
    assert flags == []
    all_flags = await read.list_flags()
    assert any(f.id == "f2" for f in all_flags)
