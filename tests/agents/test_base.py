import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, AgentRemark, ThreadPlanted
from novelizer.agents.base import BaseAgent
from novelizer.agents.schemas import ThreadIntent, CausalIntent
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
    assert log[0].payload == {"id": "the-locket", "chapter_id": "c1", "note": "reappears", "source": "declared", "evidence": ""}


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
    assert log[0].payload == {"id": "the-locket", "chapter_id": "c1", "note": "still going", "source": "declared", "evidence": ""}


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
    assert log[0].payload == {"id": "the-heir-lives", "character_id": "mara", "chapter_id": "c2", "note": "found the letter", "source": "declared", "evidence": ""}


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
    assert log[0].payload == {"id": "the-heir-lives", "chapter_id": "c5", "note": "told the crowd", "evidence": ""}


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
    assert log[0].payload == {"cause_chapter_id": "c1", "effect_chapter_id": "c3", "note": "fire forces the move", "source": "declared", "evidence": ""}


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


async def test_commit_thread_intents_defaults_source_to_declared(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_thread_intents([ThreadIntent(action="plant", name="A Thread")], active_thread_ids=set())
    await proj.catch_up()
    log = await events.events_since(0, event_types=[EventType.THREAD_PLANTED])
    assert len(log) == 1
    assert log[0].payload["source"] == "declared"


async def test_commit_thread_intents_accepts_explicit_source(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_thread_intents(
        [ThreadIntent(action="plant", name="A Thread")], active_thread_ids=set(), source="mined",
    )
    await proj.catch_up()
    log = await events.events_since(0, event_types=[EventType.THREAD_PLANTED])
    assert len(log) == 1
    assert log[0].payload["source"] == "mined"


async def test_commit_knowledge_intents_accepts_explicit_source(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="character_keeper")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="learn", id="the-heir-lives", character_id="mara")],
        active_secret_ids={"the-heir-lives"}, source="mined",
    )
    await proj.catch_up()
    log = await events.events_since(0, event_types=[EventType.SECRET_LEARNED])
    assert len(log) == 1
    assert log[0].payload["source"] == "mined"


async def test_commit_causal_intents_accepts_explicit_source(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_causal_intents(
        [CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2")],
        valid_chapter_ids={"c1", "c2"}, source="mined",
    )
    await proj.catch_up()
    log = await events.events_since(0, event_types=[EventType.CAUSAL_EDGE_DECLARED])
    assert len(log) == 1
    assert log[0].payload["source"] == "mined"


class _CapturingRecorder:
    """Test double for TelemetryRecorder: records emits, tracks nothing."""

    def __init__(self):
        self.emitted = []  # list of (event_type, aggregate_id, payload)

    async def emit(self, event_type, aggregate_id, payload):
        self.emitted.append((event_type, aggregate_id, payload))

    def in_llm_call(self, run_id):
        return False


async def test_run_once_emits_started_and_finished_with_one_run_id():
    from novelizer.telemetry.events import TelemetryEventType

    class Quiet(BaseAgent):
        async def _run(self):
            pass

    agent = Quiet(runner=None, read_store=None, committer=None, interval=0, name="quiet")
    rec = _CapturingRecorder()
    agent.telemetry = rec
    await agent.run_once()
    types = [t for t, _, _ in rec.emitted]
    assert types == [TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FINISHED]
    started, finished = rec.emitted[0][2], rec.emitted[1][2]
    assert started.run_id == finished.run_id != ""
    assert started.agent_name == "quiet"
    assert finished.duration_s >= 0.0


async def test_run_once_sets_ambient_run_context_during_run_and_resets_after():
    from novelizer.run_context import current_run_id, current_agent_name

    seen = {}

    class Peek(BaseAgent):
        async def _run(self):
            seen["run_id"] = current_run_id.get()
            seen["agent"] = current_agent_name.get()

    agent = Peek(runner=None, read_store=None, committer=None, interval=0, name="peek")
    await agent.run_once()  # works with telemetry=None too
    assert seen["run_id"] is not None
    assert seen["agent"] == "peek"
    assert current_run_id.get() is None
    assert current_agent_name.get() == ""


async def test_run_once_crash_emits_run_failed_and_reraises():
    from novelizer.telemetry.events import TelemetryEventType

    class Boom(BaseAgent):
        async def _run(self):
            raise ValueError("kaboom")

    agent = Boom(runner=None, read_store=None, committer=None, interval=0, name="boom")
    rec = _CapturingRecorder()
    agent.telemetry = rec
    with pytest.raises(ValueError, match="kaboom"):
        await agent.run_once()
    types = [t for t, _, _ in rec.emitted]
    assert types == [TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FAILED]
    failed = rec.emitted[1][2]
    assert failed.error_type == "ValueError" and "kaboom" in failed.error_message
    assert failed.phase == "agent"  # recorder reports no open LLM call


async def test_run_once_crash_inside_open_llm_call_reports_llm_call_phase():
    class InCall(_CapturingRecorder):
        def in_llm_call(self, run_id):
            return True

    class Boom(BaseAgent):
        async def _run(self):
            raise ValueError("mid-call")

    agent = Boom(runner=None, read_store=None, committer=None, interval=0, name="boom")
    rec = InCall()
    agent.telemetry = rec
    with pytest.raises(ValueError):
        await agent.run_once()
    assert rec.emitted[1][2].phase == "llm_call"


async def test_run_once_without_telemetry_is_silent_and_still_runs():
    ran = []

    class Quiet(BaseAgent):
        async def _run(self):
            ran.append(True)

    agent = Quiet(runner=None, read_store=None, committer=None, interval=0, name="quiet")
    await agent.run_once()
    assert ran == [True]


def test_seconds_until_ready_counts_down_and_floors_at_zero():
    a = BaseAgent(runner=None, read_store=None, committer=None, interval=10, name="x")
    a.mark_ran(now=100)
    assert a.seconds_until_ready(now=104) == 6
    assert a.seconds_until_ready(now=115) == 0

from novelizer.agents.schemas import ThemeIntent


async def test_commit_theme_intents_introduce_mints_id(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_theme_intents(
        [ThemeIntent(action="introduce", title="Loss of Innocence")], active_theme_ids=set(),
    )
    await proj.catch_up()
    theme = await read.get_theme("loss-of-innocence")
    assert theme is not None
    assert theme.title == "Loss of Innocence"


async def test_commit_theme_intents_develop_cites_existing_id(stack):
    events, proj, read, committer = stack
    from novelizer.canon.events import ThemeIntroduced
    await events.append(EventType.THEME_INTRODUCED, "t1", ThemeIntroduced(id="t1", title="Loss"))
    await proj.catch_up()
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    await agent._commit_theme_intents(
        [ThemeIntent(action="develop", id="t1", note="deepens")], active_theme_ids={"t1"}, chapter_id="c1",
    )
    await proj.catch_up()
    theme = await read.get_theme("t1")
    assert theme.touch_count == 1


async def test_commit_theme_intents_develop_unknown_id_dropped_with_warning(stack, caplog):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    with caplog.at_level("WARNING"):
        await agent._commit_theme_intents(
            [ThemeIntent(action="develop", id="ghost")], active_theme_ids=set(),
        )
    assert await events.events_since(0) == []
    assert any("ghost" in rec.message for rec in caplog.records)


async def test_commit_theme_intents_introduce_collision_downgrades_to_develop(stack):
    events, proj, read, committer = stack
    from novelizer.canon.events import ThemeIntroduced
    await events.append(EventType.THEME_INTRODUCED, "loss", ThemeIntroduced(id="loss", title="Loss"))
    await proj.catch_up()
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_theme_intents(
        [ThemeIntent(action="introduce", title="Loss")], active_theme_ids={"loss"},
    )
    log = await events.events_since(0, event_types=[EventType.THEME_INTRODUCED, EventType.THEME_DEVELOPED])
    assert len(log) == 2
    assert log[-1].event_type == EventType.THEME_DEVELOPED
    assert log[-1].payload["id"] == "loss"


async def test_commit_theme_intents_accepts_explicit_source(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_theme_intents(
        [ThemeIntent(action="introduce", title="X")], active_theme_ids=set(), source="mined",
    )
    await proj.catch_up()
    log = await events.events_since(0, event_types=[EventType.THEME_INTRODUCED])
    assert len(log) == 1
    assert log[0].payload["source"] == "mined"


async def test_commit_theme_intents_noop_on_empty_list(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_theme_intents([], active_theme_ids=set())
    assert await events.events_since(0) == []


async def test_commit_theme_intents_introduce_files_similarity_suggestion_retcon(stack, tmp_path):
    from novelizer.store.embeddings import EmbeddingStore
    from novelizer.store.models import FlagStatus
    from tests.conftest import FakeEmbeddingFunction

    events, proj, read, committer = stack
    embedding_store = EmbeddingStore(path=str(tmp_path), embedding_function=FakeEmbeddingFunction())
    from novelizer.store.models import ThemeRecord
    await embedding_store.upsert_theme(ThemeRecord(id="loss", title="The Cost of Ambition"))
    from novelizer.canon.events import ThemeIntroduced
    await events.append(EventType.THEME_INTRODUCED, "loss", ThemeIntroduced(id="loss", title="The Cost of Ambition"))
    await proj.catch_up()

    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_theme_intents(
        [ThemeIntent(action="introduce", title="The Price of Ambition")],
        active_theme_ids={"loss"},
        embedding_store=embedding_store,
    )
    await proj.catch_up()

    # No auto-merge: the new theme still commits as its own distinct id.
    new_theme = await read.get_theme("the-price-of-ambition")
    assert new_theme is not None

    reqs = await read.list_flags(category="thematic", status=FlagStatus.open)
    assert len(reqs) == 1
    assert reqs[0].category == "thematic"
    assert "[source: theme_similarity]" in reqs[0].description
    assert "loss" in reqs[0].description
    assert "The Cost of Ambition" in reqs[0].description
    embedding_store.close()


async def test_commit_theme_intents_introduce_noop_when_no_embedding_store(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_theme_intents(
        [ThemeIntent(action="introduce", title="Unwatched Theme")], active_theme_ids=set(),
    )
    await proj.catch_up()
    theme = await read.get_theme("unwatched-theme")
    assert theme is not None


async def test_commit_knowledge_intents_normalizes_character_id_casing(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="character_keeper")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="learn", id="s1", character_id="Kestrel")],
        active_secret_ids={"s1"}, allowed_actions=frozenset({"learn"}),
    )
    log = await events.events_since(0, event_types=[EventType.SECRET_LEARNED])
    assert len(log) == 1
    assert log[0].payload["character_id"] == "kestrel"


async def test_commit_knowledge_intents_normalizes_id_casing_for_membership_check(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="uses", id="S1", character_id="kestrel")],
        active_secret_ids={"s1"},
    )
    log = await events.events_since(0, event_types=[EventType.SECRET_REFERENCED])
    assert len(log) == 1
    assert log[0].payload["id"] == "s1"


async def test_commit_thread_intents_normalizes_touch_id_casing(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    await agent._commit_thread_intents(
        [ThreadIntent(action="touch", id="T1")], active_thread_ids={"t1"},
    )
    log = await events.events_since(0, event_types=[EventType.THREAD_TOUCHED])
    assert len(log) == 1
    assert log[0].payload["id"] == "t1"


async def test_commit_theme_intents_normalizes_develop_id_casing(stack, caplog):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    with caplog.at_level("WARNING"):
        await agent._commit_theme_intents(
            [ThemeIntent(action="develop", id="Loss")], active_theme_ids={"loss"},
        )
    log = await events.events_since(0, event_types=[EventType.THEME_DEVELOPED])
    assert len(log) == 1
    assert log[0].payload["id"] == "loss"


async def test_commit_causal_intents_normalizes_chapter_id_casing(stack, caplog):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_causal_intents(
        [CausalIntent(cause_chapter_id="ABC123", effect_chapter_id="def456")],
        valid_chapter_ids={"abc123", "def456"},
    )
    log = await events.events_since(0, event_types=[EventType.CAUSAL_EDGE_DECLARED])
    assert len(log) == 1
    assert log[0].payload["cause_chapter_id"] == "abc123"
    assert log[0].payload["effect_chapter_id"] == "def456"


def test_guarded_line_returns_labeled_value_when_present():
    assert BaseAgent._guarded_line("In character", "gruff and terse") == "\n\nIn character: gruff and terse"


def test_guarded_line_returns_empty_when_value_falsy():
    assert BaseAgent._guarded_line("In character", "") == ""


from novelizer.agents.base import DEFAULT_PASS_REMARK, PASS_BACKOFF_MULTIPLIER


def test_note_pass_extends_backoff_beyond_interval():
    agent = BaseAgent(runner=None, read_store=None, committer=None, interval=100)
    agent.mark_ran(1000.0)
    agent.note_pass(now=1000.0)
    # Normal interval has elapsed at t=1100, but the pass backoff (3x) has not.
    assert not agent.ready_for_interval(1100.0)
    assert agent.seconds_until_ready(1100.0) == 200.0
    assert agent.ready_for_interval(1300.0)


def test_note_pass_defaults_to_monotonic_clock():
    agent = BaseAgent(runner=None, read_store=None, committer=None, interval=100)
    agent.note_pass()
    import time
    assert agent._backoff_until > time.monotonic()


def test_no_pass_means_plain_interval_gate():
    agent = BaseAgent(runner=None, read_store=None, committer=None, interval=100)
    agent.mark_ran(1000.0)
    assert agent.ready_for_interval(1100.0)


def test_pass_constants():
    assert PASS_BACKOFF_MULTIPLIER == 3
    assert DEFAULT_PASS_REMARK == "Nothing needs my attention — carry on with the story."


class _WatermarkAgent(BaseAgent):
    def __init__(self, fp):
        super().__init__(runner=None, read_store=None, committer=None, interval=0)
        self.fp = fp

    async def _fingerprint(self):
        return self.fp

    async def readiness(self) -> float:
        return await self._gate_on_watermark(0.5)


async def test_watermark_zeroes_readiness_until_state_changes():
    agent = _WatermarkAgent((1, "ch1"))
    assert await agent.readiness() == 0.5      # never ran: full score
    await agent._record_watermark()
    assert await agent.readiness() == 0.0      # same state: gated
    agent.fp = (2, "ch2")
    assert await agent.readiness() == 0.5      # external change: restored


async def test_default_fingerprint_disables_watermarking():
    agent = BaseAgent(runner=None, read_store=None, committer=None, interval=0)
    await agent._record_watermark()
    assert await agent._gate_on_watermark(0.7) == 0.7
