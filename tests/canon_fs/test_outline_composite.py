import os
import tempfile

import pytest
from deepagents.backends import CompositeBackend

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon_fs.backend import CanonBackend
from novelizer.canon_fs.outline import OutlineBackend
from novelizer.store.models import Chapter


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


def build_composite(read):
    return CompositeBackend(
        default=CanonBackend(read),
        routes={"/outline/": OutlineBackend(read)},
    )


async def test_outline_route_reads_through_composite(stack):
    events, proj, read = stack
    await proj.catch_up()
    composite = build_composite(read)
    result = await composite.aread("/outline/blueprint.md")
    assert result.error is None
    assert "No blueprint adopted." in result.file_data["content"]


async def test_default_route_still_serves_canon(stack):
    events, proj, read = stack
    ch = Chapter(id="ch1", title="The Drowned Bell", prose="Mara heard the bell.")
    await events.append(EventType.CHAPTER_CREATED, ch.id, ch)
    await proj.catch_up()
    composite = build_composite(read)
    result = await composite.aread("/chapters/001-the-drowned-bell.md")
    assert result.error is None
    assert "id: ch1" in result.file_data["content"]


async def test_ls_outline_reprepends_prefix(stack):
    events, proj, read = stack
    await proj.catch_up()
    composite = build_composite(read)
    result = await composite.als("/outline")
    paths = {e["path"] for e in result.entries}
    assert "/outline/blueprint.md" in paths
    assert "/outline/beats.md" in paths
