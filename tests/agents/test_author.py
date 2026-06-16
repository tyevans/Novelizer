import pytest
from unittest.mock import AsyncMock, MagicMock
from novelizer.agents.author import Author
from novelizer.agents.base import AgentState
from novelizer.store.models import Chapter, WorldEntry, Character, DirectorSignal, SignalKind


@pytest.fixture
def store():
    s = MagicMock()
    s.list_world_entries = AsyncMock(return_value=[])
    s.list_characters = AsyncMock(return_value=[])
    s.list_chapters = AsyncMock(return_value=[])
    s.list_unconsumed_signals = AsyncMock(return_value=[])
    s.consume_signal = AsyncMock()
    s.save_chapter = AsyncMock()
    s.db = MagicMock()
    s.db.count_draft_chapters = AsyncMock(return_value=0)
    return s


async def test_readiness_low_when_many_drafts(store):
    store.db.count_draft_chapters = AsyncMock(return_value=5)
    agent = Author(store=store, min_interval=0)
    score = await agent.readiness_check()
    assert score < 0.5


async def test_readiness_high_when_no_drafts(store):
    store.db.count_draft_chapters = AsyncMock(return_value=0)
    agent = Author(store=store, min_interval=0)
    score = await agent.readiness_check()
    assert score == 1.0


async def test_commit_saves_chapter(store):
    agent = Author(store=store, min_interval=0)
    chapter = Chapter(title="Ch 1", prose="It began in darkness.")
    state = AgentState(agent_name="author")
    state.context["new_chapter"] = chapter
    await agent.commit(state)
    store.save_chapter.assert_awaited_once_with(chapter)


async def test_commit_noop_when_no_chapter(store):
    agent = Author(store=store, min_interval=0)
    state = AgentState(agent_name="author")
    state.context["new_chapter"] = None
    await agent.commit(state)
    store.save_chapter.assert_not_awaited()
