import pytest
from unittest.mock import AsyncMock, MagicMock
from novelizer.agents.character_keeper import CharacterKeeper
from novelizer.agents.base import AgentState
from novelizer.store.models import Character, Chapter, RetconRequest


@pytest.fixture
def store():
    s = MagicMock()
    s.list_characters = AsyncMock(return_value=[])
    s.list_chapters = AsyncMock(return_value=[])
    s.save_character = AsyncMock()
    s.save_retcon_request = AsyncMock()
    s.list_unconsumed_signals = AsyncMock(return_value=[])
    s.consume_signal = AsyncMock()
    return s


async def test_readiness_no_characters(store):
    agent = CharacterKeeper(store=store, min_interval=0)
    score = await agent.readiness_check()
    assert score == 0.5  # always somewhat ready


async def test_poll_fetches_characters_and_chapters(store):
    char = Character(name="Maren", traits="Brave")
    chapter = Chapter(title="Ch 1", prose="Maren ran into the fire.")
    store.list_characters = AsyncMock(return_value=[char])
    store.list_chapters = AsyncMock(return_value=[chapter])
    agent = CharacterKeeper(store=store, min_interval=0)
    state = AgentState(agent_name="character_keeper")
    await agent.poll(state)
    assert len(state.context["characters"]) == 1
    assert len(state.context["recent_chapters"]) == 1


async def test_commit_saves_updated_characters(store):
    char = Character(name="Maren", traits="Brave", arc_status="hero's journey")
    agent = CharacterKeeper(store=store, min_interval=0)
    state = AgentState(agent_name="character_keeper")
    state.context["updated_characters"] = [char]
    state.context["retcon_requests"] = []
    await agent.commit(state)
    store.save_character.assert_awaited_once_with(char)
