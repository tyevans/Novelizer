import pytest
from novelizer.agents.intents import (
    commit_thread_intents, commit_theme_intents, commit_knowledge_intents, commit_causal_intents,
    commit_promise_intents,
)
from novelizer.agents.schemas import ThreadIntent, ThemeIntent, KnowledgeIntent, CausalIntent, PromiseIntent
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


@pytest.mark.asyncio
async def test_make_mints_slug_and_commits_promise_made():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="make", name="The Sealed Letter", kind="plant", thread_id="t1")],
        active_promise_ids=set(), active_thread_ids={"t1"},
        chapter_id="ch1",
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert (name, event_type) == ("author", EventType.PROMISE_MADE)
    assert payload.id == "the-sealed-letter"
    assert payload.kind == "plant"
    assert payload.thread_id == "t1"
    assert payload.chapter_id == "ch1"


@pytest.mark.asyncio
async def test_make_with_unknown_thread_id_drops_the_link_but_keeps_the_promise():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="make", name="The Sealed Letter", thread_id="ghost")],
        active_promise_ids=set(), active_thread_ids=set(),
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.PROMISE_MADE
    assert c.commits[0][3].thread_id == ""


@pytest.mark.asyncio
async def test_make_collision_downgrades_to_progress():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="make", name="The Sealed Letter")],
        active_promise_ids={"the-sealed-letter"}, active_thread_ids=set(),
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.PROMISE_PROGRESSED


@pytest.mark.asyncio
async def test_citing_actions_drop_unknown_ids_with_no_commit():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="progress", id="ghost"),
         PromiseIntent(action="pay", id="ghost"),
         PromiseIntent(action="release", id="ghost")],
        active_promise_ids=set(), active_thread_ids=set(),
    )
    assert len(c.commits) == 0


@pytest.mark.asyncio
async def test_pay_and_release_commit_terminal_events():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="pay", id="the-sealed-letter"),
         PromiseIntent(action="release", id="the-sealed-letter", note="red herring")],
        active_promise_ids={"the-sealed-letter"}, active_thread_ids=set(),
    )
    assert len(c.commits) == 2
    assert c.commits[0][1] == EventType.PROMISE_PAID
    assert c.commits[1][1] == EventType.PROMISE_RELEASED
    assert c.commits[1][3].reason == "red herring"


@pytest.mark.asyncio
async def test_blank_name_make_is_dropped():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="make", name="")],
        active_promise_ids=set(), active_thread_ids=set(),
    )
    assert len(c.commits) == 0
