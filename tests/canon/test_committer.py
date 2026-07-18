import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.committer import Committer, GatingCommitter
from novelizer.canon.events import EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.policy import AutonomyPolicy
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
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


class AlwaysGate:
    async def is_gated(self, agent_name, event_type):
        return True


class NeverGate:
    async def is_gated(self, agent_name, event_type):
        return False


@pytest.fixture
async def gating_stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_gating_committer_queues_proposal_when_gated(gating_stack):
    events, proj, read = gating_stack
    committer = GatingCommitter(events, AlwaysGate())
    ch = Chapter(id="c1", title="One", prose="p")
    await committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
    await proj.catch_up()
    assert await read.list_chapters() == []
    props = await read.list_proposals(status="open")
    assert len(props) == 1
    assert props[0].proposing_agent == "author"
    assert props[0].target_event_type == EventType.CHAPTER_CREATED
    assert props[0].target_aggregate_id == "c1"
    assert props[0].payload["title"] == "One"


async def test_gating_committer_commits_directly_when_not_gated(gating_stack):
    events, proj, read = gating_stack
    committer = GatingCommitter(events, NeverGate())
    ch = Chapter(id="c2", title="Two", prose="p")
    await committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
    await proj.catch_up()
    chapters = await read.list_chapters()
    assert len(chapters) == 1 and chapters[0].title == "Two"
    assert await read.list_proposals() == []


async def test_gating_committer_with_real_policy_gates_by_level(gating_stack):
    events, proj, read = gating_stack
    await events.append(EventType.AUTONOMY_CHANGED, "singleton",
                         AutonomyState(global_level=AutonomyLevel.gated_canon))
    await proj.catch_up()
    committer = GatingCommitter(events, AutonomyPolicy(read))
    ch = Chapter(id="c3", title="Three", prose="p")
    await committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
    await proj.catch_up()
    assert await read.list_chapters() == []
    assert len(await read.list_proposals(status="open")) == 1
