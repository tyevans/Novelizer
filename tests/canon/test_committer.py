import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter


@pytest.fixture
async def events():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    es = EventStore(path); await es.init()
    yield es
    await es.close(); os.unlink(path)


async def test_commit_appends_the_real_event(events):
    c = Committer(events)
    await c.commit("author", EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="p"))
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.CHAPTER_CREATED
    assert log[0].aggregate_id == "ch1"
    assert log[0].payload["title"] == "One"
