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
    def __init__(self, events, scheduler):
        self.events = events; self.scheduler = scheduler; self.read = None


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


from novelizer.canon.autonomy import AutonomyLevel, AutonomyState, Proposal
from novelizer.store.models import Chapter


async def test_autonomy_appends_global_level_change(stack):
    events, proj, read = stack
    await commands.autonomy(events, AutonomyState(global_level=AutonomyLevel.gated_canon))
    await proj.catch_up()
    st = await read.get_autonomy_state()
    assert st.global_level == AutonomyLevel.gated_canon


async def test_approve_and_reject_via_command_layer(stack):
    events, proj, read = stack
    ch = Chapter(id="c1", title="One", prose="p")
    proposal = Proposal(proposing_agent="author", target_event_type="chapter.created",
                         target_aggregate_id="c1", payload=ch.model_dump(mode="json"))
    from novelizer.canon.events import EventType
    await events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal)
    await proj.catch_up()
    result = await commands.approve(events, read, proposal.id)
    await proj.catch_up()
    assert "approved" in result.lower()
    assert len(await read.list_chapters()) == 1

    proposal2 = Proposal(proposing_agent="editor", target_event_type="chapter.status_changed",
                          target_aggregate_id="c1", payload={"id": "c1", "editorial_status": "reviewed"})
    await events.append(EventType.PROPOSAL_CREATED, proposal2.id, proposal2)
    await proj.catch_up()
    result2 = await commands.reject(events, read, proposal2.id)
    assert "rejected" in result2.lower()

    assert "not found" in (await commands.approve(events, read, "missing-id")).lower()


async def test_dispatch_routes_autonomy_and_approve_reject(stack):
    events, proj, read = stack
    sched = FakeScheduler()
    rt = FakeRuntime(events, sched)
    rt.read = read
    result = await commands.dispatch(rt, "autonomy gated_canon")
    await proj.catch_up()
    assert "gated_canon" in result
    st = await read.get_autonomy_state()
    assert st.global_level == AutonomyLevel.gated_canon

    result_agent = await commands.dispatch(rt, "autonomy full_auto retconner")
    await proj.catch_up()
    st2 = await read.get_autonomy_state()
    assert st2.global_level == AutonomyLevel.gated_canon
    assert st2.overrides["retconner"] == AutonomyLevel.full_auto
    assert "retconner" in result_agent

    ch = Chapter(id="c9", title="Nine", prose="p")
    from novelizer.canon.events import EventType
    proposal = Proposal(proposing_agent="author", target_event_type="chapter.created",
                         target_aggregate_id="c9", payload=ch.model_dump(mode="json"))
    await events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal)
    await proj.catch_up()
    result3 = await commands.dispatch(rt, f"approve {proposal.id}")
    assert "approved" in result3.lower()
