import os
import tempfile

import pytest
from deepagents.backends import CompositeBackend, StateBackend

from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon_fs.backend import CanonBackend


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
        routes={"/workspace/": StateBackend()},
    )


def test_workspace_route_present_and_state_backend(stack):
    # StateBackend requires LangGraph runtime context for actual read/write
    # (see deepagents.backends.state.StateBackend._get_config), which is not
    # available in a plain pytest harness. We assert route wiring/type here;
    # the write/read round-trip is exercised implicitly by real agent runs
    # inside a LangGraph graph.
    composite = CompositeBackend(default=CanonBackend(None), routes={"/workspace/": StateBackend()})
    assert "/workspace/" in composite.routes
    assert isinstance(composite.routes["/workspace/"], StateBackend)


async def test_workspace_write_outside_graph_raises_runtime_error(stack):
    _events, proj, read = stack
    await proj.catch_up()
    composite = build_composite(read)
    with pytest.raises(RuntimeError):
        await composite.awrite("/workspace/notes.md", "hello")
