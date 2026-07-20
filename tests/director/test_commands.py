import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.canon.proposal_service import ProposalService
from novelizer.store.models import SignalKind
from novelizer.director import commands


class FakeScheduler:
    def __init__(self): self.paused = set()
    def pause_agent(self, n): self.paused.add(n)
    def resume_agent(self, n): self.paused.discard(n)


class FakeRuntime:
    def __init__(self, events, scheduler):
        self.events = events; self.scheduler = scheduler; self.read = None
        self.proposals = ProposalService(events)


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


async def test_dispatch_accepts_colon_prefixed_commands(stack):
    events, proj, read = stack
    rt = FakeRuntime(events, FakeScheduler())
    assert "seed injected" in (await commands.dispatch(rt, ":seed a storm")).lower()
    assert "focus set" in (await commands.dispatch(rt, ":focus Mira")).lower()
    await proj.catch_up()
    assert len(await read.list_unconsumed_signals()) == 2
    await commands.dispatch(rt, ":pause editor")
    assert "editor" in rt.scheduler.paused


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
    proposals = ProposalService(events)
    result = await commands.approve(proposals, read, proposal.id)
    await proj.catch_up()
    assert "approved" in result.lower()
    assert len(await read.list_chapters()) == 1

    proposal2 = Proposal(proposing_agent="editor", target_event_type="chapter.status_changed",
                          target_aggregate_id="c1", payload={"id": "c1", "editorial_status": "reviewed"})
    await events.append(EventType.PROPOSAL_CREATED, proposal2.id, proposal2)
    await proj.catch_up()
    result2 = await commands.reject(proposals, read, proposal2.id)
    assert "rejected" in result2.lower()

    assert "not found" in (await commands.approve(proposals, read, "missing-id")).lower()


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


async def test_dispatch_routes_retarget(stack):
    events, proj, read = stack
    from novelizer.canon.events import BlueprintAdopted
    rt = FakeRuntime(events, FakeScheduler())
    rt.read = read

    await events.append(
        EventType.BLUEPRINT_ADOPTED, "b1",
        BlueprintAdopted(blueprint_id="b1", framework="six-position", target_chapter_count=20, beats=[]),
    )
    await proj.catch_up()

    result = await commands.dispatch(rt, "retarget 30")
    await proj.catch_up()
    assert "30" in result
    blueprint = await read.get_active_blueprint()
    assert blueprint.target_chapter_count == 30


async def test_plan_thread_resolution_appends_event_and_projects_window(stack):
    events, proj, read = stack
    from novelizer.store.models import ThreadRecord
    from novelizer.canon.events import ThreadPlanted

    await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="The Ledger"))
    await proj.catch_up()

    result = await commands.plan_thread_resolution(events, read, "t1", 18, 20, "gate scene")
    await proj.catch_up()

    assert "18" in result and "20" in result
    thread = await read.get_thread("t1")
    assert thread.window_lo == 18
    assert thread.window_hi == 20
    assert thread.planned_payoff_note == "gate scene"


async def test_plan_thread_resolution_rejects_unknown_and_terminal_ids(stack):
    events, proj, read = stack
    from novelizer.canon.events import ThreadPlanted, ThreadPaidOff

    result = await commands.plan_thread_resolution(events, read, "missing-id", 1, 2)
    assert "no such thread" in result.lower() or "not found" in result.lower()
    await proj.catch_up()
    assert (await read.get_thread("missing-id")) is None

    await events.append(EventType.THREAD_PLANTED, "t2", ThreadPlanted(id="t2", name="Paid"))
    await events.append(EventType.THREAD_PAID_OFF, "t2", ThreadPaidOff(id="t2"))
    await proj.catch_up()

    result2 = await commands.plan_thread_resolution(events, read, "t2", 1, 2)
    assert "paid_off" in result2.lower() or "terminal" in result2.lower() or "already" in result2.lower()
    await proj.catch_up()
    thread = await read.get_thread("t2")
    assert thread.window_lo == 0
    assert thread.window_hi == 0


async def test_plan_thread_resolution_rejects_inverted_window(stack):
    events, proj, read = stack
    from novelizer.canon.events import ThreadPlanted

    await events.append(EventType.THREAD_PLANTED, "t3", ThreadPlanted(id="t3", name="Inverted"))
    await proj.catch_up()

    result = await commands.plan_thread_resolution(events, read, "t3", 20, 18)
    assert "invalid" in result.lower() or "window" in result.lower()
    await proj.catch_up()
    thread = await read.get_thread("t3")
    assert thread.window_lo == 0
    assert thread.window_hi == 0


async def test_retarget_blueprint_appends_event_and_projects(stack):
    events, proj, read = stack
    from novelizer.canon.events import BlueprintAdopted

    await events.append(
        EventType.BLUEPRINT_ADOPTED, "b1",
        BlueprintAdopted(blueprint_id="b1", framework="six-position", target_chapter_count=20, beats=[]),
    )
    await proj.catch_up()

    result = await commands.retarget_blueprint(events, read, 30)
    await proj.catch_up()

    assert "30" in result and result.startswith("blueprint retargeted")
    blueprint = await read.get_active_blueprint()
    assert blueprint.target_chapter_count == 30


async def test_retarget_blueprint_rejects_no_active_blueprint(stack):
    events, proj, read = stack
    result = await commands.retarget_blueprint(events, read, 30)
    assert "no active blueprint" in result.lower()


async def test_retarget_blueprint_rejects_too_small_count(stack):
    events, proj, read = stack
    from novelizer.canon.events import BlueprintAdopted

    await events.append(
        EventType.BLUEPRINT_ADOPTED, "b1",
        BlueprintAdopted(blueprint_id="b1", framework="six-position", target_chapter_count=20, beats=[]),
    )
    await proj.catch_up()

    result = await commands.retarget_blueprint(events, read, 2)
    await proj.catch_up()
    assert "invalid" in result.lower() or "3" in result
    blueprint = await read.get_active_blueprint()
    assert blueprint.target_chapter_count == 20


async def test_plan_secret_reveal_appends_and_rejects_revealed(stack):
    events, proj, read = stack
    from novelizer.canon.events import SecretCreated, SecretRevealed

    await events.append(EventType.SECRET_CREATED, "s1", SecretCreated(id="s1", title="Who killed the duke"))
    await proj.catch_up()

    result = await commands.plan_secret_reveal(events, read, "s1", 5, 7)
    await proj.catch_up()
    assert "5" in result and "7" in result
    secret = await read.get_secret("s1")
    assert secret.reveal_window_lo == 5
    assert secret.reveal_window_hi == 7

    await events.append(EventType.SECRET_REVEALED, "s1", SecretRevealed(id="s1"))
    await proj.catch_up()

    result2 = await commands.plan_secret_reveal(events, read, "s1", 1, 2)
    assert "revealed" in result2.lower() or "already" in result2.lower()
    await proj.catch_up()
    secret2 = await read.get_secret("s1")
    assert secret2.reveal_window_lo == 5
    assert secret2.reveal_window_hi == 7

    result3 = await commands.plan_secret_reveal(events, read, "missing-id", 1, 2)
    assert "no such secret" in result3.lower() or "not found" in result3.lower()


async def test_seed_story_dir_appends_seed_event_without_runtime(tmp_path):
    from novelizer.director.commands import seed_story_dir
    from novelizer.settings.story_dir import create_story

    sd = create_story(tmp_path / "s", title="S")
    await seed_story_dir(sd, "a tired thief takes one last job")

    events = EventStore(str(sd.db_path))
    await events.init()
    try:
        stored = await events.events_since(0)
    finally:
        await events.close()
    assert len(stored) == 1
    assert stored[0].event_type == EventType.DIRECTOR_SIGNAL_CREATED
    assert stored[0].payload["kind"] == SignalKind.seed.value
    assert stored[0].payload["body"] == "a tired thief takes one last job"
