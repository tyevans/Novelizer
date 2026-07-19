import pytest
from novelizer.agents.intents import (
    commit_thread_intents, commit_theme_intents, commit_knowledge_intents, commit_causal_intents,
)
from novelizer.agents.schemas import ThreadIntent, ThemeIntent, KnowledgeIntent, CausalIntent
from novelizer.canon.events import EventType


class FakeCommitter:
    def __init__(self):
        self.commits = []

    async def commit(self, agent_name, event_type, aggregate_id, payload):
        self.commits.append((agent_name, event_type, aggregate_id, payload))


@pytest.mark.asyncio
async def test_thread_plant_mints_and_touch_requires_known_id():
    c = FakeCommitter()
    await commit_thread_intents(
        c, "author",
        [ThreadIntent(action="plant", name="The Broken Seal"),
         ThreadIntent(action="touch", id="nonexistent")],
        active_thread_ids=set(),
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert (name, event_type) == ("author", EventType.THREAD_PLANTED)
    assert payload.id == "the-broken-seal"


@pytest.mark.asyncio
async def test_thread_plant_collision_downgrades_to_touch():
    c = FakeCommitter()
    await commit_thread_intents(
        c, "author",
        [ThreadIntent(action="plant", name="The Broken Seal")],
        active_thread_ids={"the-broken-seal"},
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.THREAD_TOUCHED


@pytest.mark.asyncio
async def test_source_is_threaded_through():
    c = FakeCommitter()
    await commit_thread_intents(
        c, "author", [ThreadIntent(action="touch", id="t1")],
        active_thread_ids={"t1"}, source="chat",
    )
    assert c.commits[0][3].source == "chat"


@pytest.mark.asyncio
async def test_knowledge_allowed_actions_restricts():
    c = FakeCommitter()
    await commit_knowledge_intents(
        c, "character_keeper",
        [KnowledgeIntent(action="reveal", id="s1"),
         KnowledgeIntent(action="learn", id="s1", character_id="c1")],
        active_secret_ids={"s1"},
        allowed_actions=frozenset({"learn"}),
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.SECRET_LEARNED


@pytest.mark.asyncio
async def test_theme_introduce_collision_downgrades_to_develop():
    c = FakeCommitter()
    await commit_theme_intents(
        c, "editor", [ThemeIntent(action="introduce", title="Grief")],
        active_theme_ids={"grief"},
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.THEME_DEVELOPED


@pytest.mark.asyncio
async def test_causal_drops_self_edge_and_unknown_ids():
    c = FakeCommitter()
    await commit_causal_intents(
        c, "editor",
        [CausalIntent(cause_chapter_id="ch1", effect_chapter_id="ch1"),
         CausalIntent(cause_chapter_id="ch1", effect_chapter_id="chX"),
         CausalIntent(cause_chapter_id="ch1", effect_chapter_id="ch2")],
        valid_chapter_ids={"ch1", "ch2"},
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.CAUSAL_EDGE_DECLARED
