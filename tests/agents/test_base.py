import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, AgentRemark, ThreadPlanted
from novelizer.agents.base import BaseAgent
from novelizer.agents.schemas import ThreadIntent
from novelizer.store.models import DirectorSignal, SignalKind


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


def test_interval_and_pause():
    a = BaseAgent(runner=None, read_store=None, committer=None, interval=10, name="x")
    assert a.name == "x"
    assert a.ready_for_interval(now=100) is True
    a.mark_ran(now=100)
    assert a.ready_for_interval(now=105) is False
    assert a.ready_for_interval(now=110) is True
    a.pause(); assert a.paused is True
    a.resume(); assert a.paused is False


async def test_consume_signals_marks_consumed(stack):
    events, proj, read, committer = stack
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.seed, body="x"))
    await proj.catch_up()
    agent = BaseAgent(runner=None, read_store=read, committer=committer, interval=0, name="a")
    sigs = await read.list_unconsumed_signals()
    await agent._consume_signals(sigs)
    await proj.catch_up()
    assert await read.list_unconsumed_signals() == []


def test_personality_defaults_to_empty_string(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="test_agent")
    assert agent.personality == ""


def test_personality_is_stored_when_provided(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="test_agent", personality="A dry wit.")
    assert agent.personality == "A dry wit."


async def test_remark_emits_agent_remarked_event_when_note_present(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._remark("Another storm brewing.")
    await proj.catch_up()
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.AGENT_REMARKED
    assert log[0].payload["agent_name"] == "author"
    assert log[0].payload["note"] == "Another storm brewing."


async def test_remark_is_a_noop_when_note_is_empty(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._remark("")
    log = await events.events_since(0)
    assert log == []


async def test_commit_thread_intents_plant_mints_slugged_id(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_thread_intents([ThreadIntent(action="plant", name="The Locket's Secret")], active_thread_ids=set())
    await proj.catch_up()
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.THREAD_PLANTED
    assert log[0].payload["id"] == "the-locket-s-secret"
    assert log[0].payload["name"] == "The Locket's Secret"


async def test_commit_thread_intents_plant_dropped_when_name_blank(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_thread_intents([ThreadIntent(action="plant", name="   ")], active_thread_ids=set())
    assert await events.events_since(0) == []


async def test_commit_thread_intents_touch_commits_when_id_known(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    await agent._commit_thread_intents(
        [ThreadIntent(action="touch", id="the-locket", note="reappears")],
        active_thread_ids={"the-locket"}, chapter_id="c1",
    )
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.THREAD_TOUCHED
    assert log[0].payload == {"id": "the-locket", "chapter_id": "c1", "note": "reappears"}


async def test_commit_thread_intents_drops_unknown_id_with_no_event(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_thread_intents(
        [ThreadIntent(action="pay_off", id="not-a-real-thread")], active_thread_ids={"the-locket"},
    )
    assert await events.events_since(0) == []


async def test_commit_thread_intents_handles_all_action_kinds(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    active = {"the-locket", "mira-revenge"}
    await agent._commit_thread_intents(
        [
            ThreadIntent(action="plant", name="New Thread"),
            ThreadIntent(action="touch", id="the-locket"),
            ThreadIntent(action="pay_off", id="mira-revenge"),
        ],
        active_thread_ids=active,
    )
    log = await events.events_since(0)
    assert [e.event_type for e in log] == [
        EventType.THREAD_PLANTED, EventType.THREAD_TOUCHED, EventType.THREAD_PAID_OFF,
    ]


async def test_commit_thread_intents_noop_on_empty_list(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_thread_intents([], active_thread_ids=set())
    assert await events.events_since(0) == []
