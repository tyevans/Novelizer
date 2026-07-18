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


async def test_commit_thread_intents_plant_colliding_with_active_id_downgrades_to_touch(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_thread_intents(
        [ThreadIntent(action="plant", name="The Locket", note="still going")],
        active_thread_ids={"the-locket"}, chapter_id="c1",
    )
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.THREAD_TOUCHED
    assert log[0].payload == {"id": "the-locket", "chapter_id": "c1", "note": "still going"}


from novelizer.agents.schemas import KnowledgeIntent
from novelizer.canon.events import SecretCreated


async def test_commit_knowledge_intents_plant_mints_slugged_id(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="plant", title="The Heir Lives")], active_secret_ids=set(),
    )
    await proj.catch_up()
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.SECRET_CREATED
    assert log[0].payload["id"] == "the-heir-lives"
    assert log[0].payload["title"] == "The Heir Lives"


async def test_commit_knowledge_intents_plant_dropped_when_title_blank(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents([KnowledgeIntent(action="plant", title="   ")], active_secret_ids=set())
    assert await events.events_since(0) == []


async def test_commit_knowledge_intents_plant_dropped_on_id_collision(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="plant", title="The Heir Lives")],
        active_secret_ids={"the-heir-lives"},
    )
    assert await events.events_since(0) == []


async def test_commit_knowledge_intents_learn_commits_when_id_known(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="character_keeper")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="learn", id="the-heir-lives", character_id="mara", note="found the letter")],
        active_secret_ids={"the-heir-lives"}, chapter_id="c2",
    )
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.SECRET_LEARNED
    assert log[0].payload == {"id": "the-heir-lives", "character_id": "mara", "chapter_id": "c2", "note": "found the letter"}


async def test_commit_knowledge_intents_learn_dropped_when_character_id_blank(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="learn", id="the-heir-lives")], active_secret_ids={"the-heir-lives"},
    )
    assert await events.events_since(0) == []


async def test_commit_knowledge_intents_drops_unknown_id_with_no_event(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="reveal", id="not-a-real-secret")], active_secret_ids={"the-heir-lives"},
    )
    assert await events.events_since(0) == []


async def test_commit_knowledge_intents_reveal_commits_without_character_id(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="reveal", id="the-heir-lives", note="told the crowd")],
        active_secret_ids={"the-heir-lives"}, chapter_id="c5",
    )
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.SECRET_REVEALED
    assert log[0].payload == {"id": "the-heir-lives", "chapter_id": "c5", "note": "told the crowd"}


async def test_commit_knowledge_intents_uses_commits_secret_referenced(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="uses", id="the-heir-lives", character_id="mara")],
        active_secret_ids={"the-heir-lives"}, chapter_id="c6",
    )
    log = await events.events_since(0)
    assert log[0].event_type == EventType.SECRET_REFERENCED
    assert log[0].payload["character_id"] == "mara"


async def test_commit_knowledge_intents_respects_allowed_actions(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="character_keeper")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="plant", title="Should Not Commit")],
        active_secret_ids=set(), allowed_actions=frozenset({"learn"}),
    )
    assert await events.events_since(0) == []


async def test_commit_knowledge_intents_noop_on_empty_list(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents([], active_secret_ids=set())
    assert await events.events_since(0) == []


from novelizer.agents.schemas import CausalIntent


async def test_commit_causal_intents_commits_when_both_chapters_valid(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_causal_intents(
        [CausalIntent(cause_chapter_id="c1", effect_chapter_id="c3", note="fire forces the move")],
        valid_chapter_ids={"c1", "c3"},
    )
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.CAUSAL_EDGE_DECLARED
    assert log[0].payload == {"cause_chapter_id": "c1", "effect_chapter_id": "c3", "note": "fire forces the move"}


async def test_commit_causal_intents_drops_self_edge(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_causal_intents(
        [CausalIntent(cause_chapter_id="c1", effect_chapter_id="c1")], valid_chapter_ids={"c1"},
    )
    assert await events.events_since(0) == []


async def test_commit_causal_intents_drops_unknown_chapter_id(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    await agent._commit_causal_intents(
        [CausalIntent(cause_chapter_id="c1", effect_chapter_id="ghost")], valid_chapter_ids={"c1"},
    )
    assert await events.events_since(0) == []


async def test_commit_causal_intents_does_not_dedup_repeated_identical_edges(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    intent = CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2")
    await agent._commit_causal_intents([intent, intent], valid_chapter_ids={"c1", "c2"})
    log = await events.events_since(0)
    assert len(log) == 2


async def test_commit_causal_intents_noop_on_empty_list(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_causal_intents([], valid_chapter_ids=set())
    assert await events.events_since(0) == []
