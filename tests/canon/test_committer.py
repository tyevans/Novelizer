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
from agent_kit import current_run_id
from novelizer.canon.events import BlueprintAdopted
from novelizer.canon.proposal_service import ProposalService


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


async def test_commit_stamps_ambient_run_id():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    try:
        token = current_run_id.set("run-7")
        try:
            await Committer(events).commit(
                "author", EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
        finally:
            current_run_id.reset(token)
        stored = (await events.events_since(0))[0]
        assert stored.run_id == "run-7"
    finally:
        await events.close()
        os.unlink(path)


async def test_commit_without_ambient_run_id_stores_none():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    try:
        await Committer(events).commit(
            "author", EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
        assert (await events.events_since(0))[0].run_id is None
    finally:
        await events.close()
        os.unlink(path)


async def test_committer_routes_blueprint_adoption_to_proposal_even_at_full_auto(gating_stack):
    events, proj, read = gating_stack
    await events.append(EventType.AUTONOMY_CHANGED, "singleton",
                         AutonomyState(global_level=AutonomyLevel.full_auto))
    await proj.catch_up()
    committer = GatingCommitter(events, AutonomyPolicy(read))
    bp = BlueprintAdopted(blueprint_id="bp1", framework="three_act", target_chapter_count=30)
    await committer.commit("plotter", EventType.BLUEPRINT_ADOPTED, "bp1", bp)
    await proj.catch_up()

    log = await events.events_since(0)
    assert all(e.event_type != EventType.BLUEPRINT_ADOPTED for e in log)
    assert any(e.event_type == EventType.PROPOSAL_CREATED for e in log)
    assert await read.get_active_blueprint() is None

    props = await read.list_proposals(status="open")
    assert len(props) == 1
    assert props[0].target_event_type == EventType.BLUEPRINT_ADOPTED
    assert props[0].target_aggregate_id == "bp1"

    await ProposalService(events).approve(props[0])
    await proj.catch_up()

    blueprint = await read.get_active_blueprint()
    assert blueprint is not None
    assert blueprint.id == "bp1"


async def test_gated_proposal_is_also_stamped_with_run_id():
    class GateAll:
        async def is_gated(self, agent_name, event_type):
            return True

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    try:
        token = current_run_id.set("run-8")
        try:
            await GatingCommitter(events, GateAll()).commit(
                "author", EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
        finally:
            current_run_id.reset(token)
        stored = (await events.events_since(0))[0]
        assert stored.event_type == EventType.PROPOSAL_CREATED
        assert stored.run_id == "run-8"
    finally:
        await events.close()
        os.unlink(path)
